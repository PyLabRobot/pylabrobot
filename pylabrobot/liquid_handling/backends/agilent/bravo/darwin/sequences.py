"""Composite motion sequences: grip, open_gripper, jog.

Ported from the ``grip`` / ``open_gripper`` / ``jog`` dispatch handlers in
``darwin_bridge.ps1`` (lines 2111-2343). These are multi-step procedures that
combine parameter-database writes (peak current / position-error-max),
force-mode instructions, and post-move validation.

Notes on simplifications from v1:
- ``jog``/``grip`` save and restore the axis's peak-current parameter around
  the force move. Callers provide the "hardware maximum current" (axis-specific
  I2T peak) since we don't yet expose that as a read-from-device property.
- Validation of ``jog`` final position against the tolerance window mirrors the
  bridge's "exceeded destination" / "unable to reach destination" checks.
- ``scan_stack_with_gripper`` is not yet ported (requires plate-sensor serial
  subcommands — see darwin_bridge.ps1:2344 for the bridge reference).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.darwin import axis as axis_module
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.motion import (
    _DEFAULT_SETTLE_POLL_MS,
    _MoveWaiter,
    _compose_send_event,
    build_load_packets,
    trigger_event,
    wait_for_ready,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.params import ParameterAccess
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.engine import GeminiEngine
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
    AxisDirection,
    InstructionTypes,
    MotorState,
    ParamDBs,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.instruction import Instruction
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import InstructionAddress


# --- Shared helpers ---------------------------------------------------------


def _convert_mm_to_percent(value_mm: float, limit_mm: float) -> float:
    """Convert an absolute limit (mm/s or mm/s²) to a 0-100 percent of axis max.

    Matches the bridge's ``Convert-ToPercent``. Returns 100.0 if the limit is
    unknown or non-positive.
    """
    if limit_mm <= 0.0 or value_mm <= 0.0:
        return 100.0
    return min(100.0, value_mm * 100.0 / limit_mm)


def _g_axis_force_percent(grip_current_amps: float) -> float:
    """Return the force-percent to use for a G-axis grip given a grip current.

    Mirrors ``Get-GAxisForcePercent`` (darwin_bridge.ps1:1661) — a linear ramp
    in amps, normalized against the 0.5A G-axis reference and scaled by 80/30.
    The input is the axis-side peak current in **amps**, not a 0-1 fraction.
    """
    if grip_current_amps < 0.0:
        grip_current_amps = 0.0
    g_reference_amps = 0.5  # Get-MaxGAxisCurrentPercent in the bridge
    if abs(grip_current_amps - g_reference_amps) < 1e-3:
        return 0.0  # bridge returns 0 when the caller is at the reference max
    force = (grip_current_amps / g_reference_amps) * 100.0 * (80.0 / 30.0)
    return max(0.0, min(100.0, force))


def _z_axis_force_percent(peak_current_amps: float) -> float:
    """Return the force-percent for a Z-axis jog given the peak current in amps.

    Piecewise-linear port of ``Get-ZAxisForcePercent`` (darwin_bridge.ps1:1675).
    The bridge hand-tuned this curve to anchor force against real tip-press
    currents (0.04A single-tip → 2%, 0.80A full 384 → 90%).
    """
    a = max(0.0, peak_current_amps)
    # Anchor points (amps, force_percent) from the bridge.
    anchors = ((0.04, 2.0), (0.07, 9.0), (0.10, 11.0), (0.16, 20.0),
               (0.30, 38.0), (0.60, 67.0), (0.80, 90.0))
    if a <= anchors[0][0]:
        return anchors[0][1]
    for (a0, f0), (a1, f1) in zip(anchors, anchors[1:]):
        if a <= a1:
            return f0 + ((a - a0) / (a1 - a0)) * (f1 - f0)
    # Beyond the top anchor the bridge extrapolates linearly from 0.60→0.80 then
    # clamps at 100%.
    a0, f0 = anchors[-2]
    a1, f1 = anchors[-1]
    extrap = f0 + ((a - a0) / (a1 - a0)) * (f1 - f0)
    return min(100.0, extrap)


_G_AXIS_REFERENCE_AMPS = 0.5  # Get-MaxGAxisCurrentPercent: the G-axis reference peak


def set_peak_current_amps(
    params: ParameterAccess,
    peak_current_amps: float,
) -> None:
    """Write ``I2T_PEAK_CURRENT`` = *peak_current_amps* and apply.

    Mirrors the bridge's ``Set-AxisPeakCurrent`` — the value is written to the
    firmware's I2T_PEAK_CURRENT parameter **in amps** with no scaling. The
    parameter type is Float32 per BLDCAxis.cs:600.
    """
    params.write_float(int(ParamDBs.I2T_PEAK_CURRENT), max(0.0, peak_current_amps))
    params.apply()


def set_position_error_max(
    params: ParameterAccess, value: float
) -> float | None:
    """Write ``POS_ERR_LIMIT`` and return the previous value (for restoration)."""
    try:
        previous = params.read_float(int(ParamDBs.POS_ERR_LIMIT))
    except Exception:
        previous = None
    params.write_float(int(ParamDBs.POS_ERR_LIMIT), value)
    params.apply()
    return previous


# --- Force-move primitive --------------------------------------------------


def force_move(
    engine: GeminiEngine,
    address: InstructionAddress,
    axis_name: str,
    target_normalized: float,
    *,
    direction: AxisDirection,
    velocity_percent: float,
    acceleration_percent: float,
    force_percent: float,
    jerk_percent: float = 100.0,
    start_event: int = 1,
    timeout_ms: int = 10_000,
) -> None:
    """Execute a force-controlled instruction: stops on force threshold.

    Builds an ``INSTR_TYPE_MOVE_TO`` instruction with non-zero ``force_percent``
    and the caller's direction, loads it onto the axis, triggers it, and waits
    for READY.

    Critical: sets ``reset_pos_after_stop`` whenever ``force_percent > 0``.
    The firmware's commanded-position counter stays at
    the full ``target_normalized`` even when the motor stopped early on a
    force threshold hit. The NEXT move sees a large commanded-vs-actual
    residual and trips POS_ERR_LIMIT (RESERVED_EVENT_ERROR, category 5
    specific 3) before a single mm of travel. The Tips On retract was
    failing on this exact pathway.
    """
    send_event = _compose_send_event(start_event)
    inst = Instruction(
        instr_type=InstructionTypes.MOVE_TO,
        velocity_percent=velocity_percent,
        acceleration_percent=acceleration_percent,
        jerk_percent=jerk_percent,
        force_percent=force_percent,
        direction=direction,
        reset_pos_after_stop=(force_percent != 0.0),
    )
    inst.volume = target_normalized
    # Match MoveAbsolute convention: trig_at = target position
    inst.trig_at_float = target_normalized

    packets = build_load_packets(address, inst, start_event, send_event)
    with _MoveWaiter(engine, send_event, axis_name, expected_src=address) as waiter:
        engine.send_multipacket(packets, timeout_ms=timeout_ms)
        trigger_event(engine, start_event)
        waiter.wait(timeout_ms)


# --- Grip (G axis: close gripper with force) -------------------------------


@dataclass
class GripParams:
    target_position: float            # normalized axis units for destination
    velocity_limit: float             # axis's VelocityLimit in native units
    acceleration_limit: float         # axis's AccelerationLimit
    grip_current_amps: float          # amps; feeds force_percent via _g_axis_force_percent
    overshoot_normalized: float       # extra past target in NORMALIZED units
                                      # (caller converts e.g. 4mm / hardware_range)
    velocity_mm: float = 500.0        # desired velocity in mm/s (converted to %)
    acceleration_mm: float = 500.0


def grip(
    engine: GeminiEngine,
    g_axis_address: InstructionAddress,
    g_axis_params: ParameterAccess,
    p: GripParams,
    *,
    timeout_ms: int = 8000,
) -> None:
    """Close the gripper with configured force. Disables motor when done.

    Mirrors the on-wire behavior of ``darwin_bridge.ps1:2111`` as validated
    against the 4to2Looping pcap: the bridge's reflective ``Set-AxisPeakCurrent``
    COM calls silently fail on this firmware, so the axis runs with firmware-
    default ``I2T_PEAK_CURRENT`` and force scaling is done entirely via the
    instruction-word ``force_percent`` byte. We therefore do **not** write
    ``I2T_PEAK_CURRENT`` here — writing an alternative peak both (a) can fail
    with OUT_OF_RANGE on the G axis on some firmware states and (b) means the
    ``finally`` restore to a cached original can NAK and mask the real error.

    ``overshoot_normalized`` is already divided by ``hardware_range`` by the
    caller, so ``farthest = target + overshoot`` stays in the normalized
    [0, 1] axis frame. We additionally clamp ``farthest`` to 1.0 — a value
    past that would exceed hardware_max and is guaranteed to be rejected by
    the firmware as NAK_OUT_OF_RANGE on the move instruction.
    """
    velocity_pct = _convert_mm_to_percent(p.velocity_mm, p.velocity_limit)
    acceleration_pct = _convert_mm_to_percent(p.acceleration_mm, p.acceleration_limit)
    force_pct = _g_axis_force_percent(p.grip_current_amps)
    farthest = min(1.0, p.target_position + p.overshoot_normalized)

    try:
        force_move(
            engine,
            g_axis_address,
            "G",
            farthest,
            direction=AxisDirection.POSITIVE,
            velocity_percent=velocity_pct,
            acceleration_percent=acceleration_pct,
            force_percent=force_pct,
            timeout_ms=timeout_ms,
        )
    finally:
        try:
            axis_module.disable(engine, g_axis_address, "G")
        except BravoError:
            pass  # non-fatal — mirror the bridge's try/catch


# --- Open gripper (G axis: move to position) ------------------------------


@dataclass
class OpenGripperParams:
    target_position: float
    current_position: float           # for direction determination
    velocity_limit: float
    acceleration_limit: float
    peak_current_amps: float          # I2T peak to set before the move (amps)
    velocity_mm: float = 60.0         # default max from the bridge
    acceleration_mm: float = 600.0


def open_gripper(
    engine: GeminiEngine,
    g_axis_address: InstructionAddress,
    g_axis_params: ParameterAccess,
    p: OpenGripperParams,
    *,
    timeout_ms: int = 6000,
) -> None:
    """Open the gripper to ``target_position``. Disables motor when done."""
    set_peak_current_amps(g_axis_params, p.peak_current_amps)
    direction = (
        AxisDirection.NEGATIVE
        if p.target_position < p.current_position
        else AxisDirection.POSITIVE
    )
    velocity_pct = _convert_mm_to_percent(p.velocity_mm, p.velocity_limit)
    acceleration_pct = _convert_mm_to_percent(p.acceleration_mm, p.acceleration_limit)

    inst = Instruction(
        instr_type=InstructionTypes.MOVE_TO,
        velocity_percent=velocity_pct,
        acceleration_percent=acceleration_pct,
        # jerk_percent=0.0 historically meant "default" in the C# API, which
        # clamps 0 → 100. Use 100 directly so the wire byte is 0xFF.
        jerk_percent=100.0,
        force_percent=0.0,
        direction=direction,
    )
    inst.volume = p.target_position
    inst.trig_at_float = p.target_position

    send_event = _compose_send_event(1)
    packets = build_load_packets(g_axis_address, inst, start_event=1, send_event=send_event)
    with _MoveWaiter(engine, send_event, "G", expected_src=g_axis_address) as waiter:
        engine.send_multipacket(packets, timeout_ms=timeout_ms)
        trigger_event(engine, 1)
        waiter.wait(timeout_ms)

    try:
        axis_module.disable(engine, g_axis_address, "G")
    except BravoError:
        pass


# --- Jog (Z or G axis: force move with validation) -------------------------


@dataclass
class JogParams:
    axis_name: str              # "Z" or "G"
    target_position: float      # normalized [0, 1] axis target
    tolerance: float            # normalized tolerance window for validation
    peak_current_amps: float    # I2T peak current to set for this jog (amps)
    velocity_mm: float          # if <=0, uses velocity_limit
    acceleration_mm: float      # if <=0, uses acceleration_limit
    velocity_limit: float
    acceleration_limit: float
    # Epsilon on the "exceeded destination" check, in normalized axis units.
    # The bridge uses 0.05 mm; callers should divide by the axis's
    # hardware_range before passing here (e.g. 0.05 / 250 for Z → 0.0002).
    # Keeping this too large (e.g. the raw 0.05 literal on a 250-mm axis)
    # makes the check trip on ~12 mm of headroom and falsely flags normal
    # near-target landings as "exceeded".
    exceed_epsilon: float = 0.0002


def jog(
    engine: GeminiEngine,
    axis_address: InstructionAddress,
    axis_params: ParameterAccess,
    p: JogParams,
    *,
    read_position: callable,    # callable(engine, address) -> float (normalized)
    timeout_ms: int = 30_000,
    settle_ms: int = 250,
) -> float:
    """Force-controlled jog on Z or G. Returns the final position (normalized).

    Mirrors the on-wire behavior of ``darwin_bridge.ps1:2255`` as observed in
    the 4to2Looping bench pcap (4 successful Tips-On cycles). Key finding
    from that capture: despite the bridge *appearing* to manipulate the peak
    current and position-error-max via COM properties, it emits **zero**
    param-DB writes to the Z axis — those reflective calls silently
    ``try { } catch { }`` through without touching the wire. The firmware
    defaults for I2T_PEAK_CURRENT and POS_ERR_LIMIT remain in force, and the
    jog's force control is done entirely via the ``force_percent`` bits of
    the instruction word.

    Writing ``POS_ERR_LIMIT=0`` in particular is unsafe for a force move —
    any tracking error exceeds zero, so the firmware powers down the motor
    the instant commanded-vs-actual diverges, raising RESERVED_EVENT_ERROR
    before tip resistance can even be sensed.

    The ``peak_current_amps`` input therefore only feeds ``force_percent``
    (via ``_z_axis_force_percent`` / ``_g_axis_force_percent``); it is NOT
    written to I2T_PEAK_CURRENT.
    """
    if p.axis_name not in ("Z", "G"):
        raise ValueError(f"jog only supported on Z and G, got {p.axis_name}")

    velocity_mm = p.velocity_mm if p.velocity_mm > 0 else p.velocity_limit
    acceleration_mm = p.acceleration_mm if p.acceleration_mm > 0 else p.acceleration_limit
    velocity_pct = _convert_mm_to_percent(velocity_mm, p.velocity_limit)
    acceleration_pct = _convert_mm_to_percent(acceleration_mm, p.acceleration_limit)
    farthest = p.target_position + max(0.0, p.tolerance)
    force_pct = (
        _z_axis_force_percent(p.peak_current_amps)
        if p.axis_name == "Z"
        else _g_axis_force_percent(p.peak_current_amps)
    )

    force_move(
        engine,
        axis_address,
        p.axis_name,
        farthest,
        direction=AxisDirection.POSITIVE,
        velocity_percent=velocity_pct,
        acceleration_percent=acceleration_pct,
        force_percent=force_pct,
        timeout_ms=timeout_ms,
    )

    final_position = read_position(engine, axis_address)
    if final_position > (farthest - p.exceed_epsilon):
        raise BravoError(
            ErrorType.EXCEEDED_DEST,
            custom_text=(
                f"Exceeded destination on {p.axis_name}. "
                f"Target={p.target_position:.2f}, actual={final_position:.2f}, "
                f"farthest={farthest:.2f}, epsilon={p.exceed_epsilon:.4f}."
            ),
        )
    if final_position < (p.target_position - p.tolerance):
        raise BravoError(
            ErrorType.UNABLE_TO_REACH_DEST,
            custom_text=(
                f"Unable to reach destination on {p.axis_name} within tolerance. "
                f"Target={p.target_position:.2f}, actual={final_position:.2f}."
            ),
        )
    time.sleep(settle_ms / 1000.0)
    return final_position
