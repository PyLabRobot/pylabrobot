"""Pure-Python DarwinController — successor to the COM-bridge implementation.

Subclasses :class:`BravoController` and implements its interface using the
GeminiEngine + darwin.* modules. No PowerShell subprocess, no COM, no DLL.

Current scope (v1, for hardware-in-the-loop validation):
  - connect / disconnect / ping / is_connected
  - firmware version read
  - enable / disable motors (per axis)
  - home_axes (commutate + home each axis)
  - move (single and multi-axis, mm/s units on input)
  - query_state (E-stop + go-button)
  - clear_go_button
  - get_position / is_axis_homed / get_park_position
  - set_light / clear_lights (native TCP)
  - detect_smart_head / read_smart_head_type / read_head_adc
  - grip / open_gripper / jog (composite sequences)
  - detect_gripper / is_plate_in_gripper / read_plate_sensor
  - scan_stack_with_gripper / send_command / reset_faults
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
  AxisMoveInfo,
  BravoController,
  FirmwareVersion,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.base import (
  JogParams as BaseJogParams,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin import axis as axis_module
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin import motion
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin import sequences
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.calibration import (
  DEFAULT_CALIBRATION,
  AxisCalibration,
  MotionLimits,
  read_motion_limits,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.params import ParameterAccess
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.topology import (
  axis_address,
  all_axes,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.waxis_config import (
  config_for_head,
  ul_to_mm,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.waxis_params import (
  apply_waxis_parameters,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.errors import BravoError, ErrorType
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.engine import GeminiEngine
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
  AxisDirection,
  CommonSubCommands,
  DarwinMasterNodeSubCommands,
  GeminiSubCommands,
  ParamDBs,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.packet import (
  MASTER_ADDRESS,
  InstructionAddress,
)
from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import LightCommandData
from pylabrobot.liquid_handling.backends.agilent.bravo.types import (
  Axis,
  DeviceStateFlag,
  GRIP_POSITION_TOLERANCE,
  GripperDetectionState,
  HeadType,
  OPEN_GRIPPER_POSITION,
  SpeedLevel,
  TICKS_PER_MM,
)

logger = logging.getLogger(__name__)


# Axes for which we cache the device's I2T_PEAK_CURRENT on connect so
# grip/jog/force-moves can scale from the original max (matches bridge
# semantics when the bridge is newly started — `$Axis.I2tPeakCurrent` is
# whatever the firmware booted with).
_PEAK_CURRENT_AXES: tuple[Axis, ...] = (Axis.G, Axis.Z, Axis.Zg, Axis.W)


def _motion_timeout_ms(
  distance_mm: float,
  velocity_mm_per_s: float,
  min_ms: int = 6000,
  margin_ms: int = 5000,
) -> int:
  """Compute a safe move timeout in ms based on travel distance + speed.

  Port of ``Get-MotionTimeoutMs`` (darwin_bridge.ps1:1182). The minimum
  speed clamp (0.1 mm/s) prevents a divide-by-zero for no-op moves.
  """
  speed = max(abs(velocity_mm_per_s), 0.1)
  travel_ms = abs(distance_mm) / speed * 1000.0
  import math

  return max(min_ms, int(math.ceil(travel_ms + margin_ms)))


@dataclass
class _AxisState:
  calibration: AxisCalibration
  limits: MotionLimits | None = None
  params: ParameterAccess | None = None
  last_command: dict[str, Any] | None = None
  # Cached device I2T_PEAK_CURRENT value at connect time (amps, or whatever
  # the firmware's native unit is). Used as the reference-max for
  # force-move sequences so grip(0.2) then grip(1.0) is idempotent.
  peak_current_max: float | None = None


class DarwinController(BravoController):
  """DARWIN controller over pure-Python Gemini wire protocol.

  Construct with a :class:`GeminiEngine` (or pass an address and port and
  the controller will build one internally).
  """

  def __init__(
    self,
    engine: GeminiEngine | None = None,
    *,
    address: str | None = None,
    port: int = 7613,
    profile: object | None = None,
  ):
    if engine is None and address is None:
      raise ValueError("DarwinController needs either engine or address")
    self._engine: GeminiEngine = engine or GeminiEngine(address, port)
    self._owns_engine = engine is None
    self._profile = profile
    self._connected = False
    self._last_error: BravoError | None = None
    self._head_type: HeadType = HeadType.HT_UNKNOWN
    self._waxis_applied_head: HeadType | None = None
    self._axes: dict[Axis, _AxisState] = {}
    # State-snapshot cache — matches bridge darwin.py:535-559 semantics so
    # higher layers (bravo.py, tasks.py) see a compatible response shape.
    self._last_snapshot: dict[str, Any] | None = None
    self._last_snapshot_at: float = 0.0
    self._init_axis_state()

  def _init_axis_state(self) -> None:
    """Build per-axis scaffolding. Calibration comes from
    :data:`DEFAULT_CALIBRATION` and may be overridden from the profile."""
    for a, cal in DEFAULT_CALIBRATION.items():
      self._axes[a] = _AxisState(calibration=cal)
    # W axis has no single calibration — placeholder; limits are loaded
    # after W-axis parameters are applied for the current head type.
    self._axes[Axis.W] = _AxisState(
      calibration=AxisCalibration(
        hardware_min=-16.48,
        hardware_max=63.52,  # HT_8_D_LT defaults; re-applied per head
      )
    )

  # ------------------------------------------------------------------
  # Connection
  # ------------------------------------------------------------------

  def open_serial(self, port: str) -> None:  # noqa: ARG002 - not supported
    raise BravoError(
      ErrorType.NODEZERO_NO_SERIAL_COMM,
      custom_text="DARWIN does not support serial; use open_tcp",
    )

  def open_tcp(self, address: str) -> None:
    """Connect to the DARWIN master node at ``<address>:7613`` (Gemini TCP)."""
    # Replace the engine's address if explicitly given
    if not self._engine.is_connected:
      # Re-build the engine if the address changed
      if address and address != getattr(self._engine._transport, "_address", None):
        self._engine = GeminiEngine(address, getattr(self._engine._transport, "_port", 7613))
        self._owns_engine = True
    try:
      self._engine.connect()
    except Exception as exc:
      self._set_error(BravoError(ErrorType.COULD_NOT_CONNECT, custom_text=str(exc)))
      raise
    self._connected = True
    self._post_connect_init()

  def _post_connect_init(self) -> None:
    """Wire up per-axis ParameterAccess objects now that the engine is live.

    Also clear each axis's instruction table — the controller preserves
    instruction-table state and event bindings across TCP sessions, so
    residual bindings from a prior bridge run can interfere with our new
    START_EVT/SEND_EVT values on event 1. The bridge dodges this by
    running continuously and allocating events carefully; we have to
    explicitly reset.
    """
    for a, state in self._axes.items():
      if state.params is None:
        state.params = ParameterAccess(self._engine, axis_address(a))
      try:
        self._engine.set_uint(
          axis_address(a),
          GeminiSubCommands.INSTR_CLEAR,
          0,
          timeout_ms=2000,
        )
      except Exception as exc:
        logger.warning("INSTR_CLEAR on %s failed (non-fatal): %s", a.name, exc)
      # Cache the device's I2T_PEAK_CURRENT reference for force-scaling axes.
      if a in _PEAK_CURRENT_AXES:
        try:
          state.peak_current_max = state.params.read_float(
            int(ParamDBs.I2T_PEAK_CURRENT), timeout_ms=2000
          )
          # INFO-level so it's visible without --verbose: this is the
          # reference-max used by grip/jog/open_gripper force scaling.
          logger.info(
            "Cached %s I2T_PEAK_CURRENT = %.6f (reference for force-scaling)",
            a.name,
            state.peak_current_max,
          )
        except Exception as exc:
          logger.warning("Could not read %s I2T_PEAK_CURRENT: %s", a.name, exc)
          state.peak_current_max = None

  def close(self) -> None:
    self._connected = False
    if self._owns_engine:
      self._engine.close()

  def ping(self) -> bool:
    """Read the master's firmware version as a liveness probe."""
    try:
      self._engine.master_get_uint(DarwinMasterNodeSubCommands.SAFETY_STATUS, timeout_ms=2000)
      return True
    except Exception as exc:
      logger.debug("Darwin ping failed: %s", exc)
      return False

  @property
  def is_connected(self) -> bool:
    return self._connected and self._engine.is_connected

  # ------------------------------------------------------------------
  # Firmware
  # ------------------------------------------------------------------

  def get_firmware_version(self) -> FirmwareVersion:
    """Read firmware version from master and each controller node."""

    def _read_version(addr: InstructionAddress) -> str:
      try:
        packed = self._engine.get_value(addr, CommonSubCommands.FW_VERSION, timeout_ms=5000)
      except Exception:
        return ""
      # 32-bit packed (major, minor, patch_hi, patch_lo)
      major = (packed >> 24) & 0xFF
      minor = (packed >> 16) & 0xFF
      patch = packed & 0xFFFF
      return f"{major}.{minor}.{patch}"

    master = _read_version(MASTER_ADDRESS)
    xy = _read_version(InstructionAddress(4))
    zw = _read_version(InstructionAddress(5))
    gzg = _read_version(InstructionAddress(6))
    # Follow the existing FirmwareVersion shape: master / sub1 / sub2
    return FirmwareVersion(master=master, sub1=f"YX={xy} ZW={zw}", sub2=f"GZg={gzg}")

  # ------------------------------------------------------------------
  # Motion limits cache (lazy-populated per axis)
  # ------------------------------------------------------------------

  def _limits(self, axis: Axis) -> MotionLimits:
    state = self._axes[axis]
    if state.limits is None:
      state.limits = read_motion_limits(state.params, state.calibration)  # type: ignore[arg-type]
    return state.limits

  def invalidate_limits(self) -> None:
    """Force re-read of motion limits after parameter changes."""
    for state in self._axes.values():
      state.limits = None

  # ------------------------------------------------------------------
  # Per-axis helpers
  # ------------------------------------------------------------------

  def _ensure_axis_enabled(self, axis: Axis) -> None:
    addr = axis_address(axis)
    if not axis_module.is_enabled(self._engine, addr):
      axis_module.enable(self._engine, addr, axis.name)

  def _ensure_waxis_params(self) -> None:
    """Write W-axis parameters if the head type changed since the last apply."""
    if self._head_type == HeadType.HT_UNKNOWN or self._head_type == self._waxis_applied_head:
      return
    w_params = self._axes[Axis.W].params
    if w_params is None:
      return
    applied = apply_waxis_parameters(w_params, self._head_type)
    if applied:
      self._waxis_applied_head = self._head_type
      self.invalidate_limits()

  # ------------------------------------------------------------------
  # Motion — move, home
  # ------------------------------------------------------------------

  def move(
    self,
    moves: list[AxisMoveInfo],
    wait: bool = True,
    timeout_ms: int = 30000,
  ) -> None:
    if not moves:
      return
    # Pre-flight: validate every absolute target against software limits
    # BEFORE we start enabling motors or sending any packets. This runs
    # even when the controller isn't connected — prevents the script
    # from driving an axis past safe bounds regardless of hardware state.
    for m in moves:
      state = self._axes[m.axis]
      if m.absolute:
        state.calibration.validate_target(m.position, m.axis.name)
    # Apply W-axis params if any move includes W
    if any(m.axis == Axis.W for m in moves):
      self._ensure_waxis_params()
    # Enable all target axes AND verify each is past commutate+home.
    # An un-initialized axis (motor state < READY) can neither accept a
    # move instruction nor echo SEND_EVT, so without this check the move
    # would silently block for the full timeout — which masks the real
    # cause (operator asked for motion before homing that axis). Fail
    # fast instead; callers such as InitializeTask._home_g wrap the
    # move in try/except specifically to catch this case.
    for m in moves:
      self._ensure_axis_enabled(m.axis)
      state = axis_module.read_motor_state(
        self._engine,
        axis_address(m.axis),
        timeout_ms=2000,
      )
      from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
        MotorState as _MS,
      )

      if int(state) < int(_MS.READY):
        raise BravoError(
          ErrorType.COULD_NOT_MOVE_TO_POSITION,
          custom_text=(
            f"{m.axis.name} axis not initialized (motor state "
            f"{state.name if hasattr(state, 'name') else state}); "
            "home the axis before issuing a move."
          ),
        )

    requests: list[motion.MoveRequest] = []
    for m in moves:
      state = self._axes[m.axis]
      limits = self._limits(m.axis)
      # Velocity/acceleration conversion: mm/s → percentage of limit
      velocity_pct = (
        100.0
        if (m.velocity <= 0 or limits.velocity <= 0)
        else min(100.0, m.velocity * 100.0 / limits.velocity)
      )
      accel_pct = (
        100.0
        if (m.acceleration <= 0 or limits.acceleration <= 0)
        else min(100.0, m.acceleration * 100.0 / limits.acceleration)
      )

      # Normalize every move to MOVE_TO (absolute) semantics, computing
      # an absolute target + direction-from-current. The MOVE_BY
      # instruction path had a subtle direction-encoding bug that made
      # negative relative jogs still go positive; collapsing both move
      # flavors onto the same MOVE_TO wire shape that's already validated
      # by pick-place + tips-on eliminates that entire failure class.
      if m.absolute:
        target_mm = m.position
      else:
        current = self.get_position(m.axis)
        target_mm = current + m.position
        state.calibration.validate_target(target_mm, m.axis.name)
      normalized = state.calibration.to_normalized(target_mm)

      current_normalized = state.calibration.to_normalized(self.get_position(m.axis))
      direction = (
        AxisDirection.NEGATIVE if normalized < current_normalized else AxisDirection.POSITIVE
      )
      from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
        InstructionTypes,
      )

      requests.append(
        motion.MoveRequest(
          address=axis_address(m.axis),
          axis_name=m.axis.name,
          target_normalized=normalized,
          velocity_percent=velocity_pct,
          acceleration_percent=accel_pct,
          instr_type=InstructionTypes.MOVE_TO,
          direction=direction,
        )
      )
      state.last_command = {
        "mode": "absolute" if m.absolute else "relative",
        "position": m.position,
        "velocity_mm": m.velocity,
        "velocity_pct": velocity_pct,
        "acceleration_mm": m.acceleration,
        "acceleration_pct": accel_pct,
      }

    motion.move_multi(self._engine, requests, wait=wait, timeout_ms=timeout_ms)

  def home_axes(self, axes: list[Axis]) -> None:
    for a in axes:
      if a == Axis.W:
        self._ensure_waxis_params()
      addr = axis_address(a)
      t = axis_module.timeouts_for(a)
      try:
        axis_module.initialize(
          self._engine,
          addr,
          a.name,
          commutate_timeout_ms=t.commutate_ms,
          home_timeout_ms=t.home_ms,
          get_estop_engaged=self._is_estop_engaged,
        )
      except BravoError as exc:
        self._set_error(exc)
        raise

  def get_position(self, axis: Axis) -> float:
    addr = axis_address(axis)
    normalized = self._engine.get_float(addr, GeminiSubCommands.POSITION)
    return self._axes[axis].calibration.from_normalized(normalized)

  def is_axis_homed(self, axis: Axis) -> bool:
    """Return True iff the axis has completed commutate + home — i.e.
    its motor-state machine is in READY / BUSY (post-homed states).

    Matches the bridge's ``$axis.IsInitialized`` at
    darwin_bridge.ps1:1996, NOT the raw HOMING_FLAG_STATE sensor
    reading (which tells you whether the physical flag sensor is
    currently actuated — True any time the axis happens to sit near
    its flag, including on cold start). Using the flag-sensor read
    would cause InitializeTask to skip Z homing whenever Z was
    parked near the top-of-travel flag, resulting in the gripper
    homing before Z was lifted.
    """
    try:
      return axis_module.is_initialized(self._engine, axis_address(axis))
    except Exception:
      return False

  def get_park_position(self, axis: Axis) -> float:
    return self._axes[axis].calibration.park_position

  # ------------------------------------------------------------------
  # Motor control
  # ------------------------------------------------------------------

  def enable_motor(self, axis: Axis) -> None:
    self._ensure_axis_enabled(axis)

  def disable_motor(self, axis: Axis) -> None:
    axis_module.disable(self._engine, axis_address(axis), axis.name)

  def reset_faults(self, axes: list[Axis]) -> None:
    # No-op on DARWIN (the bridge also treats this as a stub).
    for a in axes:
      axis_module.reset_faults(self._engine, axis_address(a))

  def is_motor_enabled(self, axis: Axis) -> bool:
    return axis_module.is_enabled(self._engine, axis_address(axis))

  # ------------------------------------------------------------------
  # Device state
  # ------------------------------------------------------------------

  def _is_estop_engaged(self) -> bool:
    try:
      status = self._engine.master_get_uint(
        DarwinMasterNodeSubCommands.SAFETY_STATUS, timeout_ms=2000
      )
    except Exception:
      return False
    return bool(status & 0x01)

  def query_state(self) -> DeviceStateFlag:
    """Return a bitfield of current state flags (E-stop, go button, …).

    v1 keeps it minimal — the upstream :class:`DeviceStateFlag` enum already
    defines the bits we care about.
    """
    flags = DeviceStateFlag(0)
    try:
      status = self._engine.master_get_uint(
        DarwinMasterNodeSubCommands.SAFETY_STATUS, timeout_ms=2000
      )
    except Exception:
      return flags
    if status & 0x01:
      # E-stop engaged — set the corresponding flag if present in enum.
      for name in ("ROBOT_DISABLE", "E_STOP"):
        if hasattr(DeviceStateFlag, name):
          flags |= getattr(DeviceStateFlag, name)
          break
    return flags

  def is_go_button_pressed(self) -> bool:
    state = self.query_state()
    for name in ("GO_BUTTON_PRESSED", "GO_BUTTON"):
      if hasattr(DeviceStateFlag, name):
        return bool(state & getattr(DeviceStateFlag, name))
    return False

  def clear_go_button(self) -> None:
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.CLEAR_GO_BTN_LATCH, 1, timeout_ms=2000)

  # ------------------------------------------------------------------
  # Safety / recovery
  # ------------------------------------------------------------------

  def recover(self, axes: list[Axis] | None = None) -> dict[Axis, str]:
    """Recover from a STOP_DISABLE / safety-trip event.

    Confirms safety status is clear, then re-enables any axis whose motor
    state is DISABLED (the state Darwin transitions axes to after a safety
    event). Mirrors the bridge's recovery sequence observed in
    ``homeafterpowercycle-movewithlightcurtain-disable-enablemotoros.pcapng``:
    SAFETY_STATUS check → SET MOTOR_STATE=ENABLE per axis.

    Returns a per-axis dict describing what action was taken:
        "enabled"   — axis was DISABLED, now enabled
        "ok"        — axis was already enabled / non-disabled
        "skipped"   — could not read state (transient connection issue)
    """
    if axes is None:
      axes = list(all_axes())

    # First make sure the safety condition has cleared
    if self._is_estop_engaged():
      raise BravoError(
        ErrorType.ROBOT_DISABLE,
        custom_text=(
          "Cannot recover: safety interlock still active "
          "(SAFETY_STATUS bit 0 set). Clear the light curtain / "
          "release E-stop, then retry."
        ),
      )

    from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
      MotorState as _MS,
    )

    result: dict[Axis, str] = {}
    for a in axes:
      addr = axis_address(a)
      try:
        state = axis_module.read_motor_state(self._engine, addr)
      except Exception as exc:
        logger.warning("recover: read state on %s failed: %s", a.name, exc)
        result[a] = "skipped"
        continue

      if state == _MS.DISABLED:
        try:
          axis_module.enable(self._engine, addr, a.name)
          result[a] = "enabled"
        except Exception as exc:
          logger.warning("recover: enable %s failed: %s", a.name, exc)
          result[a] = f"failed: {exc}"
      else:
        result[a] = "ok"
    return result

  # ------------------------------------------------------------------
  # Lights
  # ------------------------------------------------------------------

  def set_light(self, command: LightCommandData) -> None:
    encoded = _encode_light_value(command)
    self._engine.master_set_uint(
      DarwinMasterNodeSubCommands.STATUS_LIGHTS, encoded, timeout_ms=2000
    )

  def clear_lights(self) -> None:
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.STATUS_LIGHTS, 0, timeout_ms=2000)

  # ------------------------------------------------------------------
  # Head / gripper detection (best-effort)
  # ------------------------------------------------------------------

  def read_head_adc(self) -> int:
    """Read the ADC-based head-count register (master subcmd 23).

    For resistor-based ("stupid") heads this value identifies the head.
    For smart heads the value is still readable but the smart-head EEPROM
    is authoritative.
    """
    return self._engine.master_get_uint(
      DarwinMasterNodeSubCommands.STUPID_HEAD_COUNTS, timeout_ms=2000
    )

  def detect_smart_head(self) -> bool:
    """True if a smart head (with EEPROM) is attached.

    Mirrors ``BravoMasterNode.HasSmartHead``: send SMART_INIT=0 to the
    master. Success means a smart head responded; NAK
    ``UNSUCCESSFUL_OPERATION`` means no smart head is present.
    """
    from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.enums import (
      CommandNAKTypes,
    )
    from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.gemini.errors import NAKError

    try:
      self._engine.master_set_uint(DarwinMasterNodeSubCommands.SMART_INIT, 0, timeout_ms=2000)
      return True
    except NAKError as exc:
      if exc.nak == CommandNAKTypes.UNSUCCESSFUL_OPERATION:
        return False
      raise

  def read_smart_head_type(self) -> int:
    """Read the head-type byte from smart-head EEPROM offset 1.

    The byte value corresponds directly to a ``HeadType`` integer (e.g.,
    3 → HT_96_D_70). Mirrors ``BravoMasterNode.SmartHeadType`` →
    ``ReadDataFromSmartHead(1, byte[1])``.

    Call ``detect_smart_head()`` first — this will raise if no smart head
    is present.
    """
    # Tell firmware: buffer 1 byte starting at EEPROM offset 1.
    self._engine.master_set_uint(
      DarwinMasterNodeSubCommands.SMART_RD_EEPROM,
      (1 << 8) | 1,
      timeout_ms=2000,
    )
    value = self._engine.master_get_uint(
      DarwinMasterNodeSubCommands.SMART_RD_EEPROM_VAL, timeout_ms=2000
    )
    return value & 0xFF

  def detect_head_type(self) -> HeadType:
    """DEPRECATED: byte → HeadType mapping is not reliable yet.

    Observed on a physical 384ST 70µL Series III head: EEPROM byte = 1.
    The Python ``HeadType`` enum is a port of the Homewood C++ HW namespace,
    but Agilent's firmware-side head-type byte encoding is a *separate*
    convention we don't have documented. Without ground truth, returning
    ``HeadType(byte)`` produces confident-but-wrong answers (byte=1 would
    return HT_8_F_50 — the Python enum's value 1, completely unrelated).

    Callers should use :meth:`read_head_identification` instead.
    """
    return HeadType.HT_UNKNOWN

  def read_head_identification(self) -> dict:
    """Read raw head-identification data without interpreting it.

    Returns::

        {
          "eeprom_byte": int | None,   # None if no smart head responded
          "adc_counts":  int,
          "has_smart_head": bool,
        }

    The EEPROM byte is the value at smart-head offset 1, corresponding
    to ``BravoMasterNode.SmartHeadType``. ADC counts (``STUPID_HEAD_COUNTS``)
    are useful for resistor-based heads or as a cross-check.

    Use this raw data plus ``--head-type`` on the user side until we have
    a verified byte→HeadType mapping table.
    """
    has_smart = self.detect_smart_head()
    eeprom_byte = self.read_smart_head_type() if has_smart else None
    adc_counts = self.read_head_adc()
    return {
      "eeprom_byte": eeprom_byte,
      "adc_counts": adc_counts,
      "has_smart_head": has_smart,
    }

  def detect_gripper(self) -> GripperDetectionState:
    # Vendor logic (BravoMasterNode.HasGripper): locate the sub-node whose
    # device list contains "Zg" and read its FirmwareVersion. Success
    # means the gripper sub-node is alive on the bus; failure (timeout or
    # NAK) means no gripper is attached. In our stack the gripper lives
    # at InstructionAddress(6); a successful FW_VERSION read is a
    # sufficient liveness proof.
    try:
      packed = self._engine.get_value(
        InstructionAddress(6),
        CommonSubCommands.FW_VERSION,
        timeout_ms=2000,
      )
    except Exception as exc:
      logger.debug("detect_gripper: FW_VERSION read failed: %s", exc)
      return GripperDetectionState.NOT_DETECTED
    return GripperDetectionState.DETECTED if packed else GripperDetectionState.NOT_DETECTED

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    g_addr = axis_address(Axis.G)
    g_state = self._axes[Axis.G]
    # Validate target BEFORE any connection work so tests and
    # pre-flight checks catch unsafe values.
    g_state.calibration.validate_target(position, "G")
    if g_state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    self._ensure_axis_enabled(Axis.G)
    g_limits = self._limits(Axis.G)
    cal = g_state.calibration

    # Map SpeedLevel → mm/s (matches bridge grip speed table)
    velocity_mm = {
      "FAST": 1000.0,
      "SLOW": 1.0,
    }.get(speed.name, 500.0)
    # Grip current in amps (bridge default profile values): 0.3A for lids,
    # 0.2A for plates. These feed the instruction-word force_percent via
    # _g_axis_force_percent; we do NOT write I2T_PEAK_CURRENT (see
    # sequences.grip docstring — bridge's reflective COM call silently
    # no-ops and the axis runs with firmware defaults).
    grip_current_amps = 0.3 if grip_lid else 0.2
    # Bridge's farthest = target_mm + 4.0 mm. Convert 4mm to normalized
    # units because GripParams works in normalized axis frame.
    overshoot_normalized = 4.0 / cal.hardware_range
    sequences.grip(
      self._engine,
      g_addr,
      g_state.params,
      sequences.GripParams(
        target_position=cal.to_normalized(position),
        velocity_limit=g_limits.velocity,
        acceleration_limit=g_limits.acceleration,
        grip_current_amps=grip_current_amps,
        overshoot_normalized=overshoot_normalized,
        velocity_mm=velocity_mm,
        acceleration_mm=500.0,
      ),
    )

  def open_gripper(self, position: float | None = None) -> None:
    g_addr = axis_address(Axis.G)
    g_state = self._axes[Axis.G]
    if g_state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    self._ensure_axis_enabled(Axis.G)
    cal = g_state.calibration
    limits = self._limits(Axis.G)
    # Default to OPEN_GRIPPER_POSITION (0.0 mm) — matches production
    # darwin.py:881 and is what the bridge + PickPlaceTask expect. Driving
    # G toward effective_software_min (-7.0 mm) is too aggressive and
    # tripped the firmware pos-error guard on a partially-closed gripper
    # during bench post-failure recovery. validate_target still catches
    # explicit unsafe values supplied by the caller.
    if position is None:
      target_mm = OPEN_GRIPPER_POSITION
    else:
      target_mm = position
    cal.validate_target(target_mm, "G")
    current_mm = self.get_position(Axis.G)
    if g_state.peak_current_max is None:
      raise BravoError(
        ErrorType.COULD_NOT_CONNECT,
        custom_text="G axis peak-current reference not cached; reconnect",
      )
    sequences.open_gripper(
      self._engine,
      g_addr,
      g_state.params,
      sequences.OpenGripperParams(
        target_position=cal.to_normalized(target_mm),
        current_position=cal.to_normalized(current_mm),
        velocity_limit=limits.velocity,
        acceleration_limit=limits.acceleration,
        peak_current_amps=g_state.peak_current_max,
      ),
    )

  def is_plate_in_gripper(self) -> bool:
    """Report whether the plate-presence sensor (on the G axis device)
    detects a plate between the gripper fingers.

    Mirrors the bridge's ``is_plate_in_gripper`` at darwin.py:889 —
    primary path reads SUBCMD_PLATE_PRESENT=76 via ``read_plate_sensor``
    with the profile's configured transient; if that fails, falls back
    to an "is G away from the OPEN_GRIPPER_POSITION" heuristic so the
    caller always gets a bool.
    """
    transient_ms = 0
    if self._profile is not None:
      transient_ms = int(
        getattr(getattr(self._profile, "safety", None), "plate_sensor_transient_ms", 0) or 0
      )
    try:
      return self.read_plate_sensor(transient_ms=transient_ms)
    except BravoError:
      try:
        pos_mm = self.get_position(Axis.G)
        tol_mm = GRIP_POSITION_TOLERANCE / TICKS_PER_MM.get(Axis.G, 944.88)
        return abs(pos_mm - OPEN_GRIPPER_POSITION) > tol_mm
      except BravoError:
        return False

  def jog(self, params: BaseJogParams) -> float:
    axis = params.axis
    if axis not in (Axis.Z, Axis.G):
      raise BravoError(
        ErrorType.DARWIN_GENERIC,
        custom_text=f"jog only supported on Z and G, got {axis.name}",
      )
    addr = axis_address(axis)
    state = self._axes[axis]
    if state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    if state.peak_current_max is None:
      raise BravoError(
        ErrorType.COULD_NOT_CONNECT,
        custom_text=f"{axis.name} axis peak-current reference not cached; reconnect",
      )
    self._ensure_axis_enabled(axis)
    cal = state.calibration
    limits = self._limits(axis)

    def read_pos_normalized(engine, a):
      raw = engine.get_float(a, GeminiSubCommands.POSITION)
      return raw  # already normalized in engine response

    target_normalized = cal.to_normalized(params.max_position)
    tolerance_normalized = params.tolerance / cal.hardware_range

    final_normalized = sequences.jog(
      self._engine,
      addr,
      state.params,
      sequences.JogParams(
        axis_name=axis.name,
        target_position=target_normalized,
        tolerance=tolerance_normalized,
        peak_current_amps=params.peak_current,
        velocity_mm=params.velocity,
        acceleration_mm=params.acceleration,
        velocity_limit=limits.velocity,
        acceleration_limit=limits.acceleration,
        # Bridge's "Exceeded destination" check uses a 0.05 mm
        # epsilon near the farthest point. Convert to normalized
        # axis units so it means the same thing here.
        exceed_epsilon=0.05 / cal.hardware_range,
      ),
      read_position=read_pos_normalized,
    )
    return cal.from_normalized(final_normalized)

  # ------------------------------------------------------------------
  # Plate sensor + stack scanning
  # ------------------------------------------------------------------
  #
  # Ports darwin_bridge.ps1:971 (Get-PlateSensorPresentDirect),
  # darwin_bridge.ps1:951 (Set-PlateSensorEnabledDirect),
  # darwin_bridge.ps1:1012 (Read-PlateSensorState),
  # darwin_bridge.ps1:989  (Get-PlateSensorPresent — read wrapper),
  # darwin_bridge.ps1:2344 (scan_stack_with_gripper state machine).
  #
  # Wire details validated from the bridge source:
  #   * Target device for subcmd=76 is the FIRST device on the DarwinGZg
  #     node — i.e. the G axis address (node=6, dev=0). The bridge calls it
  #     Find-PlateSensorDevice and picks $gripperNode.Devices[0].
  #   * Enable:  SET   subcmd=76 val=2
  #     Disable: SET   subcmd=76 val=0
  #     Read:    GET   subcmd=76 → uint; bit 0 = plate present
  #   * The bridge also calls Enable-PlatePresenceSensor (a reflective
  #     master-node property) before each scan, but that call silently
  #     fails on this firmware (same pattern as Set-AxisPeakCurrent — see
  #     sequences.jog docstring). We therefore skip it: only the G-device
  #     SET subcmd=76 has actual wire effect, and scans succeed without it.

  _PLATE_SENSOR_ADDRESS = property(lambda self: axis_address(Axis.G))

  def _plate_sensor_enable(self, enabled: bool) -> bool:
    """Send SET subcmd=76 to the G device. 2=enable, 0=disable."""
    try:
      self._engine.set_uint(
        axis_address(Axis.G),
        GeminiSubCommands.PLATE_PRESENT,
        2 if enabled else 0,
        timeout_ms=5000,
      )
      return True
    except Exception as exc:
      logger.debug("plate-sensor enable=%s failed: %s", enabled, exc)
      return False

  def _read_plate_sensor_state(
    self,
    *,
    max_attempts: int = 1,
    retry_delay_ms: int = 0,
    retry_until_present: bool = False,
  ) -> dict[str, Any]:
    """Mirror the bridge's ``Read-PlateSensorState`` (darwin_bridge.ps1:1012).

    Returns ``{"read": bool, "present": bool, "errors": list[str]}``. The
    ``read`` flag indicates whether we got a successful sensor read at
    all; ``present`` is only meaningful when ``read`` is True.
    """
    errors: list[str] = []
    read = False
    present = False
    attempts = max(1, max_attempts)
    addr = axis_address(Axis.G)
    for i in range(attempts):
      value: int | None = None
      try:
        value = self._engine.get_value(
          addr,
          GeminiSubCommands.PLATE_PRESENT,
          timeout_ms=5000,
        )
      except Exception as exc:
        errors.append(f"gripper_sensor_read={exc}")
      if value is not None:
        present = bool(value & 1)
        read = True
        if present or not retry_until_present:
          break
      if retry_delay_ms > 0 and i < attempts - 1:
        time.sleep(retry_delay_ms / 1000.0)
    return {"read": read, "present": present, "errors": errors}

  def read_plate_sensor(self, transient_ms: int = 0) -> bool:
    """Enable the plate sensor, wait ``transient_ms``, read, disable.

    Returns True if a plate is detected. Raises :class:`BravoError` with
    :class:`ErrorType.COULD_NOT_QUERY_STATE` if every attempt to read the
    sensor failed (matches the bridge's Get-PlateSensorPresent, which
    throws in that case — darwin_bridge.ps1:1007).
    """
    self._plate_sensor_enable(True)
    try:
      if transient_ms > 0:
        time.sleep(transient_ms / 1000.0)
      result = self._read_plate_sensor_state(
        max_attempts=3,
        retry_delay_ms=100,
        retry_until_present=True,
      )
    finally:
      self._plate_sensor_enable(False)
    if not result["read"]:
      detail = ("; errors=" + " | ".join(result["errors"])) if result["errors"] else ""
      raise BravoError(
        ErrorType.COULD_NOT_QUERY_STATE,
        custom_text=f"Could not read plate sensor state from G axis{detail}",
      )
    return bool(result["present"])

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient_ms: int = 0,
  ) -> dict[str, Any]:
    """Stepwise Zg descent + plate-sensor poll to find the top of a stack.

    Direct port of darwin_bridge.ps1:2344-2459. Behavior:

    1. Move Zg to ``start_zg`` (absolute).
    2. Enable the plate sensor, optionally sleep ``transient_ms``.
    3. Initial read with 3 attempts / 100 ms delay / RetryUntilPresent —
       if nothing reads, raise.
    4. If a plate is already detected at the start, back off upward in
       10 mm steps until the sensor clears (or Zg reaches -20 mm).
    5. Descend stepwise toward ``end_zg``; after each step, poll the
       sensor with 3 attempts / 10 ms delay / RetryUntilPresent. First
       'present' hit terminates with ``detected=True``. If we reach
       end_zg without a hit, return ``detected=False``.
    6. Always disable the plate sensor in a ``finally`` block.

    Speed-dependent step size: FAST=1.0, SLOW=0.25, else 0.5 mm.
    Velocity: FAST=20, SLOW=2, else 5 mm/s.
    Acceleration: min(80, axis acceleration limit) mm/s².

    Returns a dict matching the bridge shape so
    ScanStackHeightTask._scan_with_plate_sensor (tasks.py:2510)
    consumes it unchanged:

        {
            "detected": bool,
            "scan_mode": "stepwise_hot_sensor",
            "elapsed_ms": int,
            "poll_count": int,
            "sensor_reads": int,
            "sensor_read_failures": int,
            "positions": {"X": ..., "Y": ..., "Z": ..., "W": ...,
                          "G": ..., "Zg": ...},
            "telemetry": {...per-axis dict...},
        }
    """
    self._ensure_axis_enabled(Axis.Zg)

    if speed == SpeedLevel.FAST:
      velocity_mm = 20.0
      step_mm = 1.0
    elif speed == SpeedLevel.SLOW:
      velocity_mm = 2.0
      step_mm = 0.25
    else:
      velocity_mm = 5.0
      step_mm = 0.5

    # Cap acceleration at 80 mm/s² like the bridge; fall back to 40 if
    # the axis's limit is non-positive.
    zg_limits = self._limits(Axis.Zg)
    accel_mm = min(80.0, zg_limits.acceleration if zg_limits.acceleration > 0 else 40.0)
    if accel_mm <= 0.0:
      accel_mm = 40.0

    sensor_read_count = 0
    sensor_read_failures = 0
    sensor_read_errors: list[str] = []
    poll_count = 0
    detected = False
    scan_started_at = time.monotonic()

    def _zg_move(target: float) -> None:
      distance = abs(target - self.get_position(Axis.Zg))
      timeout_ms = _motion_timeout_ms(
        distance,
        velocity_mm,
        min_ms=4000,
        margin_ms=1000,
      )
      self.move(
        [
          AxisMoveInfo(
            axis=Axis.Zg,
            position=target,
            velocity=velocity_mm,
            acceleration=accel_mm,
            absolute=True,
          )
        ],
        wait=True,
        timeout_ms=timeout_ms,
      )

    # Step 1: seek to start_zg.
    start_distance = abs(start_zg - self.get_position(Axis.Zg))
    start_timeout_ms = _motion_timeout_ms(
      start_distance,
      velocity_mm,
      min_ms=6000,
      margin_ms=2000,
    )
    self.move(
      [
        AxisMoveInfo(
          axis=Axis.Zg,
          position=start_zg,
          velocity=velocity_mm,
          acceleration=accel_mm,
          absolute=True,
        )
      ],
      wait=True,
      timeout_ms=start_timeout_ms,
    )

    # Step 2+: enable sensor, then scan.
    self._plate_sensor_enable(True)
    try:
      if transient_ms > 0:
        time.sleep(transient_ms / 1000.0)

      # Initial read — must succeed or we bail.
      initial = self._read_plate_sensor_state(
        max_attempts=3,
        retry_delay_ms=100,
        retry_until_present=True,
      )
      if not initial["read"]:
        detail = "; errors=" + " | ".join(initial["errors"]) if initial["errors"] else ""
        raise BravoError(
          ErrorType.COULD_NOT_QUERY_STATE,
          custom_text=(f"Could not read plate sensor state from Darwin during scan{detail}"),
        )
      sensor_read_count += 1
      present = bool(initial["present"])

      # Step 4: already on a plate? Back off upward in 10 mm chunks.
      while present and self.get_position(Axis.Zg) > -20.0:
        target = max(-20.0, self.get_position(Axis.Zg) - 10.0)
        _zg_move(target)
        back = self._read_plate_sensor_state(
          max_attempts=3,
          retry_delay_ms=10,
        )
        if back["read"]:
          sensor_read_count += 1
          present = bool(back["present"])
        else:
          sensor_read_failures += 1
          for err in back["errors"]:
            if len(sensor_read_errors) < 6:
              sensor_read_errors.append(err)
        if not present:
          break
        if target <= -20.0:
          break

      # Step 5: descend stepwise toward end_zg, polling at each step.
      while self.get_position(Axis.Zg) < end_zg:
        target = min(end_zg, self.get_position(Axis.Zg) + step_mm)
        _zg_move(target)
        poll_count += 1
        step_read = self._read_plate_sensor_state(
          max_attempts=3,
          retry_delay_ms=10,
          retry_until_present=True,
        )
        if step_read["read"]:
          sensor_read_count += 1
          if step_read["present"]:
            detected = True
            break
        else:
          sensor_read_failures += 1
          for err in step_read["errors"]:
            if len(sensor_read_errors) < 6:
              sensor_read_errors.append(err)
        if target >= end_zg:
          break
    finally:
      self._plate_sensor_enable(False)

    if sensor_read_count <= 0:
      detail = "; errors=" + " | ".join(sensor_read_errors) if sensor_read_errors else ""
      raise BravoError(
        ErrorType.COULD_NOT_QUERY_STATE,
        custom_text=(f"Could not read plate sensor state from Darwin master during scan{detail}"),
      )

    elapsed_ms = int((time.monotonic() - scan_started_at) * 1000)
    # Invalidate snapshot cache — positions changed.
    self._last_snapshot = None
    return {
      "detected": bool(detected),
      "scan_mode": "stepwise_hot_sensor",
      "elapsed_ms": elapsed_ms,
      "poll_count": poll_count,
      "sensor_reads": sensor_read_count,
      "sensor_read_failures": sensor_read_failures,
      "positions": self.get_all_positions(),
      "telemetry": self._axis_telemetry(),
    }

  # ------------------------------------------------------------------
  # Bulk position + state snapshot
  # ------------------------------------------------------------------

  def get_all_positions(self) -> dict[str, float]:
    """Return the current position of every axis in mm.

    Mirrors darwin_bridge.ps1:491 (Get-AllPositions). The bridge does the
    same naive per-axis loop; no multipacket read is available for
    position queries in the Gemini protocol.
    """
    out: dict[str, float] = {}
    for a in (Axis.X, Axis.Y, Axis.Z, Axis.W, Axis.G, Axis.Zg):
      try:
        out[a.name] = float(self.get_position(a))
      except Exception as exc:
        logger.debug("get_all_positions: %s read failed: %s", a.name, exc)
    return out

  def _motor_states(self) -> dict[str, bool]:
    """Per-axis enabled flag. Mirrors Get-MotorStates (ps1:502)."""
    out: dict[str, bool] = {}
    for a in (Axis.X, Axis.Y, Axis.Z, Axis.W, Axis.G, Axis.Zg):
      try:
        out[a.name] = bool(self.is_motor_enabled(a))
      except Exception:
        out[a.name] = False
    return out

  def _state_flags(self) -> int:
    """Bridge-compatible bitfield: 0x01=E-stop, 0x02=motor_power (any
    axis enabled), 0x04=go button. Mirrors Get-StateFlags (ps1:1777).

    The bridge reads go-button state via a reflective master-node
    property (``$bravoMaster.IsGoButtonDepressed``) for which we don't
    have a verified on-wire subcommand mapping. For now the bit stays
    0 — the Go button is an operator-advance input, not a motion
    input, so workflows don't depend on it being read live.
    """
    flags = 0
    if self._is_estop_engaged():
      flags |= 0x01
    if any(self._motor_states().values()):
      flags |= 0x02
    return flags

  def _axis_telemetry(self) -> dict[str, dict[str, Any]]:
    """Per-axis diagnostics. Mirrors Get-AxisTelemetry (ps1:1098).

    Populated from what we can cheaply observe without extra round
    trips: position, enabled, calibration limits (hw/sw min/max),
    motion limits, and last_command. Fields we don't yet sample
    (measured_current, peak_current, current_position_error) are
    omitted rather than faked — consumers (tasks.py:_fmt_telemetry) use
    ``isinstance`` checks on each field, so missing keys are fine.
    """
    telem: dict[str, dict[str, Any]] = {}
    for a in (Axis.X, Axis.Y, Axis.Z, Axis.W, Axis.G, Axis.Zg):
      state = self._axes.get(a)
      if state is None:
        continue
      cal = state.calibration
      entry: dict[str, Any] = {
        "hardware_minimum": cal.hardware_min,
        "hardware_maximum": cal.hardware_max,
        "software_minimum": cal.effective_software_min,
        "software_maximum": cal.effective_software_max,
      }
      try:
        entry["position"] = float(self.get_position(a))
      except Exception:
        pass
      try:
        entry["enabled"] = bool(self.is_motor_enabled(a))
      except Exception:
        pass
      if state.limits is not None:
        entry["velocity_limit"] = state.limits.velocity
        entry["acceleration_limit"] = state.limits.acceleration
      if state.peak_current_max is not None:
        entry["peak_current"] = state.peak_current_max
      if state.last_command is not None:
        entry["last_command"] = dict(state.last_command)
      telem[a.name] = entry
    return telem

  def get_state_snapshot(self, max_age_s: float = 0.15) -> dict[str, Any]:
    """Composite snapshot: positions + motor states + state flags +
    head/gripper flags + per-axis telemetry.

    Mirrors darwin.py:535 (bridge) and the bridge's "snapshot" dispatch
    at ps1:1828. Cached for ``max_age_s`` seconds so rapid callers
    (web UI poll, tasks.py logging) don't hammer the wire.
    """
    now = time.monotonic()
    if self._last_snapshot is not None and (now - self._last_snapshot_at) <= max_age_s:
      return dict(self._last_snapshot)

    positions = self.get_all_positions()
    motors = self._motor_states()
    flags = self._state_flags()

    # head_attached / gripper_present via master reads — both can fail
    # on early connects; fall back to the cached self._head_type.
    head_attached = False
    try:
      head_attached = bool(self.detect_smart_head())
    except Exception:
      head_attached = self._head_type != HeadType.HT_UNKNOWN
    gripper_present = False
    try:
      gripper_present = self.detect_gripper() == GripperDetectionState.DETECTED
    except Exception:
      gripper_present = False

    snapshot = {
      "positions": positions,
      "motors_enabled": motors,
      "head_attached": head_attached,
      "gripper_present": gripper_present,
      "go_button_pressed": bool(flags & int(DeviceStateFlag.GO_BUTTON)),
      "robot_disabled": bool(flags & int(DeviceStateFlag.ROBOT_DISABLE)),
      "telemetry": self._axis_telemetry(),
    }
    self._last_snapshot = snapshot
    self._last_snapshot_at = now
    return dict(snapshot)

  # ------------------------------------------------------------------
  # Send command (generic dispatch — not used by Darwin)
  # ------------------------------------------------------------------

  def send_command(self, command_id: int, data: bytes = b"", timeout_ms: int = 2000) -> bytes:
    """Legacy agile-style command passthrough.

    DARWIN has no generic command dispatch — the few legacy CommandIDs
    that higher-level init code still issues are mapped to either a
    no-op or to an equivalent native method, matching the bridge's
    semantics at darwin.py:955.
    """
    from pylabrobot.liquid_handling.backends.agilent.bravo.protocol.commands import CommandID

    if command_id == CommandID.CLEAR_MOTOR_POWER_FAULT:
      # DARWIN firmware has no motor-power-fault concept
      # reachable over Gemini — the bridge treats this as a no-op.
      logger.debug("Darwin: CLEAR_MOTOR_POWER_FAULT is a no-op")
      return b""
    if command_id == CommandID.CLEAR_GO_BUTTON:
      self.clear_go_button()
      return b""
    if command_id == CommandID.CLEAR_LIGHTS:
      self.clear_lights()
      return b""
    raise BravoError(
      ErrorType.DARWIN_SOFTWARE_INTERNAL,
      custom_text=(f"DARWIN command passthrough is not implemented for 0x{int(command_id):02X}."),
    )

  # ------------------------------------------------------------------
  # Error tracking
  # ------------------------------------------------------------------

  def _set_error(self, error: BravoError) -> None:
    self._last_error = error
    logger.error("Darwin error: %s", error)

  @property
  def last_error(self) -> BravoError | None:
    return self._last_error

  # ------------------------------------------------------------------
  # Head-type management (used by profile / upstream code)
  # ------------------------------------------------------------------

  def set_head_type(self, head_type: HeadType) -> None:
    """Declare the currently-attached pipette head.

    This updates the W-axis hardware range and µL→mm factor, and marks the
    57-parameter W-axis table for re-apply on the next W move. Must be
    called before any aspirate/dispense so the plunger positions are
    interpreted correctly.
    """
    self._head_type = head_type
    self._waxis_applied_head = None  # force param re-apply on next W move
    # Update the W-axis calibration with this head's hardware range
    cfg = config_for_head(head_type)
    if cfg is not None:
      self._axes[Axis.W].calibration = cfg.calibration()
      # Hardware range changed → any cached motion limits are stale
      self._axes[Axis.W].limits = None

  def ul_to_mm(self, volume_ul: float) -> float:
    """Convert pipette volume (µL) to W-axis mm for the current head."""
    return ul_to_mm(volume_ul, self._head_type)

  # ------------------------------------------------------------------
  # W-axis pipetting (aspirate / dispense) — convenience wrappers on move()
  # ------------------------------------------------------------------

  def aspirate(
    self,
    volume_ul: float,
    *,
    velocity_mm: float = 50.0,
    acceleration_mm: float = 500.0,
    timeout_ms: int = 15_000,
  ) -> None:
    """Draw liquid by extending the plunger ``volume_ul`` above park.

    Positions the W axis at ``+volume_ul × factor`` mm from park. Requires
    :func:`set_head_type` to have been called so the µL→mm factor is known.
    """
    if self._head_type == HeadType.HT_UNKNOWN:
      raise BravoError(
        ErrorType.DARWIN_GENERIC,
        custom_text="aspirate requires set_head_type() first",
      )
    target_mm = self.ul_to_mm(volume_ul)
    self.move(
      [
        AxisMoveInfo(
          axis=Axis.W,
          position=target_mm,
          velocity=velocity_mm,
          acceleration=acceleration_mm,
          absolute=True,
        )
      ],
      wait=True,
      timeout_ms=timeout_ms,
    )

  def dispense(
    self,
    volume_ul: float,
    *,
    velocity_mm: float = 50.0,
    acceleration_mm: float = 500.0,
    timeout_ms: int = 15_000,
  ) -> None:
    """Expel ``volume_ul`` by driving the plunger down toward 0.

    Moves W from its current position back to 0 (park). To dispense a
    specific volume, call aspirate() first to set the starting position
    and then dispense(0) or a smaller volume to leave residual.
    """
    target_mm = self.ul_to_mm(volume_ul)
    self.move(
      [
        AxisMoveInfo(
          axis=Axis.W,
          position=target_mm,
          velocity=velocity_mm,
          acceleration=acceleration_mm,
          absolute=True,
        )
      ],
      wait=True,
      timeout_ms=timeout_ms,
    )


# ----------------------------------------------------------------------------
# Light-encoding helper — lifted from pybravo/controllers/darwin.py::
# _encode_native_light_value so we don't depend on the flat file.
# ----------------------------------------------------------------------------


def _encode_light_value(command: LightCommandData) -> int:
  colors = int(command.light)
  blue = 100 if (colors & 0x08) else 0
  red = 0
  green = 0
  code = colors & 0x07
  if code == 1:
    red = 100
  elif code == 2:
    red, green = 25, 100
  elif code == 3:
    red, green = 100, 65
  elif code == 4:
    green = 100
  elif code == 5:
    red, green = 100, 100
  elif code == 6:
    red, green = 20, 100
  elif code == 7:
    red, green = 80, 100
  period = int(command.period_ms or 0)
  duty = float(command.duty_cycle or 0.0)
  if duty == 1.0 or period > 2000:
    blink_rate = 0
  elif 0.7 < duty < 0.9:
    blink_rate = int(period / 20) | 0x80
  else:
    blink_rate = int((period + 20) / 40) & 0x7F
  return ((red & 0xFF) << 24) | ((green & 0xFF) << 16) | ((blue & 0xFF) << 8) | (blink_rate & 0xFF)
