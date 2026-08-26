"""Composite motion sequences: grip, open_gripper, jog.

These are multi-step procedures that combine parameter-database writes
(peak current / position-error-max), force-mode instructions, and post-move
validation.

Notes on simplifications:

- :func:`jog`/:func:`grip` do not need to save and restore the axis's peak-
  current parameter around the force move; force scaling is driven entirely
  through the instruction-word ``force_percent`` byte from a caller-supplied
  peak-current value.
- :func:`jog` validates its final position against a tolerance window,
  reporting "exceeded destination" and "unable to reach destination"
  separately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..errors import BravoError, ErrorType
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import AxisDirection, InstructionTypes, ParamDBs
from ..protocol.gemini.instruction import Instruction
from ..protocol.gemini.packet import InstructionAddress
from . import axis as axis_module
from .motion import _compose_send_event, _MoveWaiter, build_load_packets, trigger_event
from .params import ParameterAccess

# --- Shared helpers -------------------------------------------------------------


def _convert_mm_to_percent(value_mm: float, limit_mm: float) -> float:
  """Convert an absolute limit (mm/s or mm/s^2) to a 0-100 percent of axis max.

  Args:
    value_mm: The desired absolute value.
    limit_mm: The axis's maximum value for the same quantity.

  Returns:
    The equivalent percentage, clamped to 100.0. Returns 100.0 if either
    input is unknown or non-positive.
  """
  if limit_mm <= 0.0 or value_mm <= 0.0:
    return 100.0
  return min(100.0, value_mm * 100.0 / limit_mm)


def _g_axis_force_percent(grip_current_amps: float) -> float:
  """Return the force-percent to use for a G-axis grip given a grip current.

  A linear ramp in amps, normalized against the 0.5A G-axis reference and
  scaled by 80/30. The input is the axis-side peak current in amps, not a
  0-1 fraction.

  Args:
    grip_current_amps: The target grip current, in amps.

  Returns:
    The instruction-word force percent, 0-100.
  """
  if grip_current_amps < 0.0:
    grip_current_amps = 0.0
  g_reference_amps = 0.5  # The G-axis reference (maximum) peak current.
  if abs(grip_current_amps - g_reference_amps) < 1e-3:
    return 0.0  # 0 when the caller is already at the reference max.
  force = (grip_current_amps / g_reference_amps) * 100.0 * (80.0 / 30.0)
  return max(0.0, min(100.0, force))


def _z_axis_force_percent(peak_current_amps: float) -> float:
  """Return the force-percent for a Z-axis jog given the peak current in amps.

  Piecewise-linear curve, hand-tuned against measured tip-press currents to
  anchor force against real tip presses (0.04A single-tip -> 2%, 0.80A full
  384 -> 90%).

  Args:
    peak_current_amps: The target peak current, in amps.

  Returns:
    The instruction-word force percent, 0-100.
  """
  a = max(0.0, peak_current_amps)
  anchors = (
    (0.04, 2.0),
    (0.07, 9.0),
    (0.10, 11.0),
    (0.16, 20.0),
    (0.30, 38.0),
    (0.60, 67.0),
    (0.80, 90.0),
  )
  if a <= anchors[0][0]:
    return anchors[0][1]
  for (a0, f0), (a1, f1) in zip(anchors, anchors[1:]):
    if a <= a1:
      return f0 + ((a - a0) / (a1 - a0)) * (f1 - f0)
  # Beyond the top anchor, extrapolate linearly from 0.60->0.80 then clamp
  # at 100%.
  a0, f0 = anchors[-2]
  a1, f1 = anchors[-1]
  extrap = f0 + ((a - a0) / (a1 - a0)) * (f1 - f0)
  return min(100.0, extrap)


_G_AXIS_REFERENCE_AMPS = 0.5  # The G-axis reference (maximum) peak current.


def set_peak_current_amps(params: ParameterAccess, peak_current_amps: float) -> None:
  """Write ``I2T_PEAK_CURRENT`` and apply.

  The value is written to the firmware's ``I2T_PEAK_CURRENT`` parameter in
  amps with no scaling. The parameter type is Float32.

  Args:
    params: The parameter accessor for the target axis's device.
    peak_current_amps: The peak current to write, in amps.
  """
  params.write_float(int(ParamDBs.I2T_PEAK_CURRENT), max(0.0, peak_current_amps))
  params.apply()


def set_position_error_max(params: ParameterAccess, value: float) -> Optional[float]:
  """Write ``POS_ERR_LIMIT`` and return its previous value.

  Args:
    params: The parameter accessor for the target axis's device.
    value: The new position-error limit to write.

  Returns:
    The previous value, for restoration, or ``None`` if it could not be
    read.
  """
  try:
    previous: Optional[float] = params.read_float(int(ParamDBs.POS_ERR_LIMIT))
  except Exception:
    previous = None
  params.write_float(int(ParamDBs.POS_ERR_LIMIT), value)
  params.apply()
  return previous


# --- Force-move primitive -----------------------------------------------------


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
  timeout: float = 10.0,
) -> None:
  """Execute a force-controlled instruction: stops on force threshold.

  Builds a ``MOVE_TO`` instruction with non-zero ``force_percent`` and the
  caller's direction, loads it onto the axis, triggers it, and waits for
  the SEND_EVT echo.

  Always sets ``reset_pos_after_stop`` whenever ``force_percent > 0``: the
  firmware's commanded-position counter stays at the full
  ``target_normalized`` even when the motor stopped early on a force
  threshold hit. Without this, the next move sees a large commanded-vs-
  actual residual and trips ``POS_ERR_LIMIT`` (a RESERVED ERROR event,
  category 5 specific 3) before a single mm of travel.

  Args:
    engine: The Gemini engine to drive the move through.
    address: The axis device's controller-tree address.
    axis_name: The axis's display name, used in error messages.
    target_normalized: The farthest target position, in normalized [0, 1]
      axis units.
    direction: Move direction.
    velocity_percent: Move velocity, 0-100% of axis max.
    acceleration_percent: Move acceleration, 0-100% of axis max.
    force_percent: Force limit, 0-100%; the move stops when this
      threshold is reached.
    jerk_percent: Move jerk, 0-100% of axis max.
    start_event: The event number to start the instruction with.
    timeout: Maximum time to wait for the move to finish, in seconds.
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
  # Match the MoveAbsolute convention: trig_at = target position.
  inst.trig_at_float = target_normalized

  packets = build_load_packets(address, inst, start_event, send_event)
  with _MoveWaiter(engine, send_event, axis_name, expected_src=address) as waiter:
    engine.send_multipacket(packets, timeout)
    trigger_event(engine, start_event)
    waiter.wait(timeout)


# --- Grip (G axis: close gripper with force) --------------------------------


@dataclass
class GripParams:
  """Parameters for a force-controlled grip move.

  Attributes:
    target_position: Destination, in normalized axis units.
    velocity_limit: The G axis's velocity ceiling, in native units.
    acceleration_limit: The G axis's acceleration ceiling.
    grip_current_amps: Grip current in amps, fed to
      :func:`_g_axis_force_percent`.
    overshoot_normalized: Extra distance past the target, in normalized
      units (the caller converts, e.g. 4mm / hardware_range).
    velocity_mm: Desired velocity in mm/s (converted to a percentage).
    acceleration_mm: Desired acceleration in mm/s^2.
  """

  target_position: float
  velocity_limit: float
  acceleration_limit: float
  grip_current_amps: float
  overshoot_normalized: float
  velocity_mm: float = 500.0
  acceleration_mm: float = 500.0


def grip(
  engine: GeminiEngine,
  g_axis_address: InstructionAddress,
  g_axis_params: ParameterAccess,
  p: GripParams,
  *,
  timeout: float = 8.0,
) -> None:
  """Close the gripper with configured force. Disables the motor when done.

  The axis runs with the firmware-default ``I2T_PEAK_CURRENT``, and force
  scaling is done entirely via the instruction-word ``force_percent`` byte.
  ``I2T_PEAK_CURRENT`` is therefore not written here: writing an alternative
  peak can fail with ``OUT_OF_RANGE`` on the G axis in some firmware
  states, and a ``finally``-block restore to a cached original could then
  itself NAK and mask the real error.

  ``overshoot_normalized`` is already divided by ``hardware_range`` by the
  caller, so ``farthest = target + overshoot`` stays in the normalized
  [0, 1] axis frame. ``farthest`` is additionally clamped to 1.0 -- a value
  past that would exceed ``hardware_max`` and is guaranteed to be rejected
  by the firmware as ``OUT_OF_RANGE`` on the move instruction.

  Args:
    engine: The Gemini engine to drive the grip through.
    g_axis_address: The G axis device's controller-tree address.
    g_axis_params: The parameter accessor for the G axis device.
    p: The grip parameters.
    timeout: Maximum time to wait for the move to finish, in seconds.
  """
  del g_axis_params  # Not written to; see the docstring above.
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
      timeout=timeout,
    )
  finally:
    try:
      axis_module.disable(engine, g_axis_address, "G")
    except BravoError:
      pass  # Non-fatal.


# --- Open gripper (G axis: move to position) --------------------------------


@dataclass
class OpenGripperParams:
  """Parameters for an open-gripper move.

  Attributes:
    target_position: Destination, in normalized axis units.
    current_position: Current position, in normalized axis units, used to
      determine move direction.
    velocity_limit: The G axis's velocity ceiling, in native units.
    acceleration_limit: The G axis's acceleration ceiling.
    peak_current_amps: I2T peak current to set before the move, in amps.
    velocity_mm: Desired velocity in mm/s (converted to a percentage).
    acceleration_mm: Desired acceleration in mm/s^2.
  """

  target_position: float
  current_position: float
  velocity_limit: float
  acceleration_limit: float
  peak_current_amps: float
  velocity_mm: float = 60.0
  acceleration_mm: float = 600.0


def open_gripper(
  engine: GeminiEngine,
  g_axis_address: InstructionAddress,
  g_axis_params: ParameterAccess,
  p: OpenGripperParams,
  *,
  timeout: float = 6.0,
) -> None:
  """Open the gripper to ``target_position``. Disables the motor when done.

  Args:
    engine: The Gemini engine to drive the move through.
    g_axis_address: The G axis device's controller-tree address.
    g_axis_params: The parameter accessor for the G axis device.
    p: The open-gripper parameters.
    timeout: Maximum time to wait for the move to finish, in seconds.
  """
  set_peak_current_amps(g_axis_params, p.peak_current_amps)
  direction = (
    AxisDirection.NEGATIVE if p.target_position < p.current_position else AxisDirection.POSITIVE
  )
  velocity_pct = _convert_mm_to_percent(p.velocity_mm, p.velocity_limit)
  acceleration_pct = _convert_mm_to_percent(p.acceleration_mm, p.acceleration_limit)

  inst = Instruction(
    instr_type=InstructionTypes.MOVE_TO,
    velocity_percent=velocity_pct,
    acceleration_percent=acceleration_pct,
    # jerk_percent=0.0 historically meant "default", which clamps 0 to 100.
    # Use 100 directly so the wire byte is 0xFF.
    jerk_percent=100.0,
    force_percent=0.0,
    direction=direction,
  )
  inst.volume = p.target_position
  inst.trig_at_float = p.target_position

  send_event = _compose_send_event(1)
  packets = build_load_packets(g_axis_address, inst, start_event=1, send_event=send_event)
  with _MoveWaiter(engine, send_event, "G", expected_src=g_axis_address) as waiter:
    engine.send_multipacket(packets, timeout)
    trigger_event(engine, 1)
    waiter.wait(timeout)

  try:
    axis_module.disable(engine, g_axis_address, "G")
  except BravoError:
    pass


# --- Jog (Z or G axis: force move with validation) ---------------------------


@dataclass
class JogParams:
  """Parameters for a force-controlled jog with post-move validation.

  Attributes:
    axis_name: ``"Z"`` or ``"G"``.
    target_position: Normalized [0, 1] axis target.
    tolerance: Normalized tolerance window for validation.
    peak_current_amps: Current used to derive the instruction's force
      percent, in amps.
    velocity_mm: Desired velocity in mm/s; ``velocity_limit`` is used
      instead if this is non-positive.
    acceleration_mm: Desired acceleration in mm/s^2; ``acceleration_limit``
      is used instead if this is non-positive.
    velocity_limit: The axis's velocity ceiling, in native units.
    acceleration_limit: The axis's acceleration ceiling.
    exceed_epsilon: Epsilon on the "exceeded destination" check, in
      normalized axis units. The check is defined as 0.05 mm; callers
      should divide by the axis's hardware_range before passing here (e.g.
      0.05 / 250 for Z). Too large a value (e.g. the raw 0.05 literal on a
      250-mm axis) makes the check trip on roughly 12mm of headroom and
      falsely flags normal near-target landings as "exceeded".
  """

  axis_name: str
  target_position: float
  tolerance: float
  peak_current_amps: float
  velocity_mm: float
  acceleration_mm: float
  velocity_limit: float
  acceleration_limit: float
  exceed_epsilon: float = 0.0002


def jog(
  engine: GeminiEngine,
  axis_address: InstructionAddress,
  axis_params: ParameterAccess,
  p: JogParams,
  *,
  read_position: Callable[[GeminiEngine, InstructionAddress], float],
  timeout: float = 30.0,
  settle: float = 0.25,
) -> float:
  """Force-controlled jog on Z or G. Returns the final position (normalized).

  This path emits no parameter-database writes to the axis: neither the
  peak current nor the position-error-max is manipulated. The firmware
  defaults for ``I2T_PEAK_CURRENT`` and ``POS_ERR_LIMIT`` remain in force,
  and the jog's force control is done entirely via the ``force_percent``
  bits of the instruction word.

  Writing ``POS_ERR_LIMIT=0`` in particular is unsafe for a force move --
  any tracking error would then exceed zero, so the firmware would power
  down the motor the instant commanded-vs-actual diverges, raising a
  RESERVED ERROR event before tip resistance can even be sensed.

  ``peak_current_amps`` therefore only feeds ``force_percent`` (via
  :func:`_z_axis_force_percent`/:func:`_g_axis_force_percent`); it is not
  written to ``I2T_PEAK_CURRENT``.

  Args:
    engine: The Gemini engine to drive the jog through.
    axis_address: The axis device's controller-tree address.
    axis_params: The parameter accessor for the axis device (unused; kept
      for a uniform sequence-function signature).
    p: The jog parameters.
    read_position: Returns the axis's current normalized position.
    timeout: Maximum time to wait for the move to finish, in seconds.
    settle: Delay after a successful jog before returning, in seconds.

  Returns:
    The axis's final normalized position.

  Raises:
    ValueError: If ``p.axis_name`` is not ``"Z"`` or ``"G"``.
    BravoError: If the final position exceeds the farthest allowed point,
      or falls short of the tolerance window around the target.
  """
  del axis_params  # Unused; see the docstring above.
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
    timeout=timeout,
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
  time.sleep(settle)
  return final_position
