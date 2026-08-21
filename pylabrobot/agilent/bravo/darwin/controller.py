"""Pure-Python controller for Darwin-generation Bravo instruments.

Implements :class:`~..controllers.base.BravoController` on top of
:class:`~..protocol.gemini.engine.GeminiEngine` and the ``darwin.*``
modules: the controller speaks the Gemini wire protocol directly over an
already-connected transport, with no external helper process.

Scope:
  - initialize / deinitialize / ping / is_connected
  - firmware version read
  - enable / disable motors (per axis)
  - home_axes (commutate + home each axis)
  - move (single- and multi-axis, mm/s units on input)
  - query_state (E-stop + go-button)
  - clear_go_button
  - get_position / is_axis_homed / get_park_position
  - set_light / clear_lights
  - detect_smart_head / read_smart_head_type / read_head_adc
  - grip / open_gripper / jog (composite sequences)
  - detect_gripper / is_plate_in_gripper / read_plate_sensor
  - scan_stack_with_gripper / send_command / reset_faults
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

from ..axis_config import AxisConfig
from ..controllers.base import AxisMoveInfo, BravoController, FirmwareVersion, JogParams
from ..errors import BravoError, ErrorType
from ..protocol.commands import CommandID, LightCommandData
from ..protocol.gemini.engine import GeminiEngine
from ..protocol.gemini.enums import (
  AxisDirection,
  CommandNAKTypes,
  CommonSubCommands,
  DarwinMasterNodeSubCommands,
  GeminiSubCommands,
  InstructionTypes,
  MotorState,
  ParamDBs,
)
from ..protocol.gemini.errors import NAKError
from ..protocol.gemini.packet import MASTER_ADDRESS, InstructionAddress
from ..transport import Transport
from ..transport.serial import SerialTransport
from ..types import (
  GRIP_POSITION_TOLERANCE,
  OPEN_GRIPPER_POSITION,
  TICKS_PER_MM,
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  HeadType,
  SpeedLevel,
  axis_display_name,
  safe_home_order,
)
from . import axis as axis_module
from . import motion, sequences
from .calibration import DEFAULT_CALIBRATION, AxisCalibration, MotionLimits, read_motion_limits
from .params import ParameterAccess
from .topology import all_axes, axis_address
from .waxis_config import config_for_head, ul_to_mm
from .waxis_params import apply_waxis_parameters

logger = logging.getLogger(__name__)


# Axes for which the device's I2T_PEAK_CURRENT is cached on connect so
# grip/jog/force-moves can scale from the original max -- i.e. whatever
# value the firmware booted with, read once per session.
_PEAK_CURRENT_AXES: tuple = ("g", "z", "zg", "w")

# SpeedLevel -> grip velocity, mm/s. Levels with no explicit entry use the
# 500.0 mm/s default.
_GRIP_SPEED_MM: Dict[str, float] = {"fast": 1000.0, "slow": 1.0}


def _motion_timeout(
  distance_mm: float,
  velocity_mm_per_s: float,
  min_s: float = 6.0,
  margin_s: float = 5.0,
) -> float:
  """Compute a safe move timeout from travel distance and speed.

  The timeout is travel time plus ``margin_s``, floored at ``min_s``. The
  minimum speed clamp (0.1 mm/s) prevents a divide-by-zero for a no-op
  move.

  Args:
    distance_mm: The travel distance, in mm.
    velocity_mm_per_s: The move velocity, in mm/s.
    min_s: The minimum timeout to return, in seconds.
    margin_s: Extra time added on top of the computed travel time, in
      seconds.

  Returns:
    The computed timeout, in seconds.
  """
  speed = max(abs(velocity_mm_per_s), 0.1)
  travel_s = abs(distance_mm) / speed
  return max(min_s, travel_s + margin_s)


@dataclass
class _AxisState:
  """Per-axis runtime state the controller keeps between calls.

  Attributes:
    calibration: The axis's normalized-position calibration.
    limits: The axis's velocity/acceleration ceilings, lazily read from
      the device and cached.
    params: The parameter accessor for the axis's device, created once
      the controller is initialized.
    last_command: A diagnostic record of the axis's most recent move
      request.
    peak_current_max: The device's ``I2T_PEAK_CURRENT`` value cached at
      connect time, used as the reference max for force-move sequences.
  """

  calibration: AxisCalibration
  limits: Optional[MotionLimits] = None
  params: Optional[ParameterAccess] = None
  last_command: Optional[Dict[str, Any]] = None
  peak_current_max: Optional[float] = None


class DarwinController(BravoController):
  """Darwin-generation Bravo controller over the pure-Python Gemini protocol.

  Constructed around an already-connected
  :class:`~..transport.base.Transport`; call :meth:`initialize` once the
  transport is set up, and :meth:`deinitialize` to release the engine's
  receive thread before the transport is torn down.
  """

  has_gripper = True
  model_name = "Bravo Darwin"

  def __init__(
    self,
    transport: Transport,
    plate_sensor_transient: float = 0.3,
    axis_config: Optional[Dict[Axis, AxisConfig]] = None,
  ):
    """Bind this controller to a transport, performing no I/O.

    Args:
      transport: The already-connected transport this controller
        communicates over. Must not be a :class:`~..transport.serial.SerialTransport`
        -- Darwin's controller tree is reachable only over TCP.
      plate_sensor_transient: How long to allow a plate-sensor reading to
        settle before treating it as final, in seconds. Used as the
        default for :meth:`is_plate_in_gripper`.
      axis_config: Per-axis travel-range overrides, keyed by axis. Only
        :attr:`~..axis_config.AxisConfig.range` is used, to override the
        corresponding axis's hardware travel limits in
        :data:`~.calibration.DEFAULT_CALIBRATION`; an axis with no entry
        keeps its default calibration.

    Raises:
      BravoError: If ``transport`` is a :class:`~..transport.serial.SerialTransport`.
    """
    if isinstance(transport, SerialTransport):
      raise BravoError(
        ErrorType.NODEZERO_NO_SERIAL_COMM,
        custom_text="Darwin does not support serial transport; use a TCP-based transport.",
      )
    super().__init__(transport)
    self._engine = GeminiEngine(transport)
    self._plate_sensor_transient = plate_sensor_transient
    self._axis_config_overrides: Dict[Axis, AxisConfig] = axis_config or {}
    self._connected = False
    self._last_error: Optional[BravoError] = None
    self._head_type: HeadType = "unknown"
    self._waxis_applied_head: Optional[HeadType] = None
    self._axes: Dict[Axis, _AxisState] = {}
    # State-snapshot cache -- shape is fixed by what higher layers expect
    # from get_state_snapshot().
    self._last_snapshot: Optional[Dict[str, Any]] = None
    self._last_snapshot_at: float = 0.0
    self._init_axis_state()

  def _init_axis_state(self) -> None:
    """Build per-axis scaffolding from :data:`~.calibration.DEFAULT_CALIBRATION`.

    The W axis has no single calibration in
    :data:`~.calibration.DEFAULT_CALIBRATION`: it gets a placeholder here,
    and its real limits are loaded once W-axis parameters are applied for
    the current head type (see :meth:`set_head_type`).
    """
    for a, cal in DEFAULT_CALIBRATION.items():
      self._axes[a] = _AxisState(calibration=self._override_calibration(a, cal))
    self._axes["w"] = _AxisState(
      calibration=self._override_calibration(
        "w",
        AxisCalibration(
          hardware_min=-16.48,
          hardware_max=63.52,  # 8_d_lt defaults; re-applied per head.
        ),
      )
    )

  def _override_calibration(self, axis: Axis, cal: AxisCalibration) -> AxisCalibration:
    """Apply this controller's axis-config travel-range override, if any.

    Args:
      axis: The axis to look up an override for.
      cal: The axis's default calibration.

    Returns:
      ``cal`` unchanged, or a copy with its hardware range replaced from
      the matching :class:`~..axis_config.AxisConfig` entry.
    """
    cfg = self._axis_config_overrides.get(axis)
    if cfg is None:
      return cal
    return replace(cal, hardware_min=cfg.range.min_pos, hardware_max=cfg.range.max_pos)

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  def initialize(self) -> None:
    """Start the engine's receive thread and bring every axis's bookkeeping up.

    Starts the Gemini engine's background receive thread, then for every
    axis: creates its :class:`~.params.ParameterAccess`, clears its
    instruction table (the controller preserves instruction-table state
    and event bindings across TCP sessions, so residual bindings from a
    prior session could otherwise interfere with this session's
    START_EVT/SEND_EVT values on event 1), and, for axes whose force-move
    sequences scale off it, caches the device's current
    ``I2T_PEAK_CURRENT``.
    """
    self._engine.start_receiving()
    self._connected = True
    for a, state in self._axes.items():
      if state.params is None:
        state.params = ParameterAccess(self._engine, axis_address(a))
      try:
        self._engine.set_uint(axis_address(a), GeminiSubCommands.INSTR_CLEAR, 0, 2.0)
      except Exception as exc:
        logger.warning("INSTR_CLEAR on %s failed (non-fatal): %s", axis_display_name(a), exc)
      if a in _PEAK_CURRENT_AXES:
        try:
          state.peak_current_max = state.params.read_float(int(ParamDBs.I2T_PEAK_CURRENT), 2.0)
          logger.info(
            "Cached %s I2T_PEAK_CURRENT = %.6f (reference for force-scaling)",
            axis_display_name(a),
            state.peak_current_max,
          )
        except Exception as exc:
          logger.warning("Could not read %s I2T_PEAK_CURRENT: %s", axis_display_name(a), exc)
          state.peak_current_max = None

  def deinitialize(self) -> None:
    """Stop the engine's receive thread. Does not touch the transport."""
    self._connected = False
    self._engine.stop_receiving()

  def ping(self) -> bool:
    """Read the master's safety status as a liveness probe."""
    try:
      self._engine.master_get_uint(DarwinMasterNodeSubCommands.SAFETY_STATUS, 2.0)
      return True
    except Exception as exc:
      logger.debug("Darwin ping failed: %s", exc)
      return False

  @property
  def is_connected(self) -> bool:
    """Whether the controller is initialized and the engine's transport is connected."""
    return self._connected and self._engine.is_connected

  # ------------------------------------------------------------------
  # Firmware
  # ------------------------------------------------------------------

  def get_firmware_version(self) -> FirmwareVersion:
    """Read the firmware version from the master and each controller node."""

    def _read_version(addr: InstructionAddress) -> str:
      """Read and format one node's packed firmware-version word.

      Args:
        addr: The node's controller-tree address.

      Returns:
        The version as ``"major.minor.patch"``, or the empty string if the
        read failed.
      """
      try:
        packed = self._engine.get_value(addr, CommonSubCommands.FW_VERSION, 5.0)
      except Exception:
        return ""
      major = (packed >> 24) & 0xFF
      minor = (packed >> 16) & 0xFF
      patch = packed & 0xFFFF
      return f"{major}.{minor}.{patch}"

    master = _read_version(MASTER_ADDRESS)
    xy = _read_version(InstructionAddress(4))
    zw = _read_version(InstructionAddress(5))
    gzg = _read_version(InstructionAddress(6))
    return FirmwareVersion(master=master, sub1=f"YX={xy} ZW={zw}", sub2=f"GZg={gzg}")

  # ------------------------------------------------------------------
  # Motion limits cache (lazy-populated per axis)
  # ------------------------------------------------------------------

  def _limits(self, axis: Axis) -> MotionLimits:
    """Return an axis's velocity/acceleration ceilings, reading them once.

    Args:
      axis: The axis to look up.

    Returns:
      The axis's cached (or freshly read) motion limits.

    Raises:
      BravoError: If the axis has no parameter accessor yet (the
        controller has not been initialized).
    """
    state = self._axes[axis]
    if state.limits is None:
      if state.params is None:
        raise BravoError(ErrorType.COULD_NOT_CONNECT)
      state.limits = read_motion_limits(state.params, state.calibration)
    return state.limits

  def invalidate_limits(self) -> None:
    """Force every axis's motion limits to be re-read on next use."""
    for state in self._axes.values():
      state.limits = None

  # ------------------------------------------------------------------
  # Per-axis helpers
  # ------------------------------------------------------------------

  def _ensure_axis_enabled(self, axis: Axis) -> None:
    """Enable an axis's motor if it is currently disabled.

    Args:
      axis: The axis to ensure is enabled.
    """
    addr = axis_address(axis)
    if not axis_module.is_enabled(self._engine, addr):
      axis_module.enable(self._engine, addr, axis_display_name(axis))

  def _ensure_waxis_params(self) -> None:
    """Write W-axis parameters if the head type changed since the last apply."""
    if self._head_type == "unknown" or self._head_type == self._waxis_applied_head:
      return
    w_params = self._axes["w"].params
    if w_params is None:
      return
    applied = apply_waxis_parameters(w_params, self._head_type)
    if applied:
      self._waxis_applied_head = self._head_type
      self.invalidate_limits()

  # ------------------------------------------------------------------
  # Motion -- move, home
  # ------------------------------------------------------------------

  def move(self, moves: List[AxisMoveInfo], wait: bool = True, timeout: float = 30.0) -> None:
    """Execute a coordinated multi-axis move.

    Args:
      moves: The per-axis targets to move to together.
      wait: Whether to block until the move finishes.
      timeout: Maximum time to wait for the move to finish, in seconds.

    Raises:
      ValueError: If an absolute target falls outside an axis's software
        limits.
      BravoError: If a target axis has not completed commutation and
        homing.
    """
    if not moves:
      return
    # Pre-flight: validate every absolute target against software limits
    # before enabling motors or sending any packets, even if the
    # controller isn't connected -- this prevents driving an axis past
    # safe bounds regardless of hardware state.
    for m in moves:
      state = self._axes[m.axis]
      if m.absolute:
        state.calibration.validate_target(m.position, axis_display_name(m.axis))
    if any(m.axis == "w" for m in moves):
      self._ensure_waxis_params()
    # Enable all target axes AND verify each is past commutate+home. An
    # uninitialized axis (motor state below READY) can neither accept a
    # move instruction nor echo SEND_EVT, so without this check the move
    # would silently block for the full timeout -- which masks the real
    # cause (motion requested before homing that axis). Fail fast instead.
    for m in moves:
      self._ensure_axis_enabled(m.axis)
      motor_state = axis_module.read_motor_state(self._engine, axis_address(m.axis), 2.0)
      if int(motor_state) < int(MotorState.READY):
        raise BravoError(
          ErrorType.COULD_NOT_MOVE_TO_POSITION,
          custom_text=(
            f"{axis_display_name(m.axis)} axis not initialized (motor state "
            f"{motor_state.name}); home the axis before issuing a move."
          ),
        )

    requests: List[motion.MoveRequest] = []
    for m in moves:
      state = self._axes[m.axis]
      limits = self._limits(m.axis)
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

      # Every move is normalized to MOVE_TO (absolute) semantics, computing
      # an absolute target and direction-from-current: collapsing both move
      # flavors onto the same MOVE_TO wire shape avoids a direction-encoding
      # class of bug that a MOVE_BY path is prone to.
      if m.absolute:
        target_mm = m.position
      else:
        current = self.get_position(m.axis)
        target_mm = current + m.position
        state.calibration.validate_target(target_mm, axis_display_name(m.axis))
      normalized = state.calibration.to_normalized(target_mm)

      current_normalized = state.calibration.to_normalized(self.get_position(m.axis))
      direction = (
        AxisDirection.NEGATIVE if normalized < current_normalized else AxisDirection.POSITIVE
      )
      requests.append(
        motion.MoveRequest(
          address=axis_address(m.axis),
          axis_name=axis_display_name(m.axis),
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

    motion.move_multi(self._engine, requests, wait=wait, timeout=timeout)

  def home_axes(self, axes: List[Axis], *, force: bool = False) -> None:
    """Commutate and home each axis, in safe order.

    Without ``force`` an axis that already reports itself initialized is
    left alone -- that is what makes start-up cheap when the instrument is
    already up. An explicit operator home must pass ``force=True``.

    Args:
      axes: The axes to home.
      force: Re-run commutation and homing even for an axis that already
        reports itself initialized.
    """
    for a in safe_home_order(axes):
      if a == "w":
        self._ensure_waxis_params()
      addr = axis_address(a)
      t = axis_module.timeouts_for(a)
      try:
        axis_module.initialize(
          self._engine,
          addr,
          axis_display_name(a),
          commutate_timeout=t.commutate,
          home_timeout=t.home,
          force=force,
          get_estop_engaged=self._is_estop_engaged,
        )
      except BravoError as exc:
        self._set_error(exc)
        raise

  def get_position(self, axis: Axis) -> float:
    """Return an axis's current position, in mm (or uL for W)."""
    addr = axis_address(axis)
    normalized = self._engine.get_float(addr, GeminiSubCommands.POSITION)
    return self._axes[axis].calibration.from_normalized(normalized)

  def is_axis_homed(self, axis: Axis) -> bool:
    """Return whether an axis has completed commutation and homing.

    This is the axis's initialized state (motor state at or beyond READY),
    not the raw home-flag sensor reading -- the sensor reads True any time
    the axis happens to sit near its flag, including on a cold start,
    which would make a caller skip homing an axis that is merely parked
    near its sensor.
    """
    try:
      return axis_module.is_initialized(self._engine, axis_address(axis))
    except Exception:
      return False

  def get_park_position(self, axis: Axis) -> float:
    """Return an axis's configured park position, in mm."""
    return self._axes[axis].calibration.park_position

  # ------------------------------------------------------------------
  # Motor control
  # ------------------------------------------------------------------

  def enable_motor(self, axis: Axis) -> None:
    """Enable an axis's motor drive."""
    self._ensure_axis_enabled(axis)

  def disable_motor(self, axis: Axis) -> None:
    """Disable an axis's motor drive."""
    axis_module.disable(self._engine, axis_address(axis), axis_display_name(axis))

  def reset_faults(self, axes: List[Axis]) -> None:
    """Clear latched fault state on the given axes (a no-op on Darwin)."""
    for a in axes:
      axis_module.reset_faults(self._engine, axis_address(a))

  def is_motor_enabled(self, axis: Axis) -> bool:
    """Return whether an axis's motor is currently enabled."""
    return axis_module.is_enabled(self._engine, axis_address(axis))

  # ------------------------------------------------------------------
  # Device state
  # ------------------------------------------------------------------

  def _is_estop_engaged(self) -> bool:
    """Return whether the master's safety status reports E-stop engaged."""
    try:
      status = self._engine.master_get_uint(DarwinMasterNodeSubCommands.SAFETY_STATUS, 2.0)
    except Exception:
      return False
    return bool(status & 0x01)

  def query_state(self) -> DeviceStateFlag:
    """Return the device's current state flags (currently just E-stop)."""
    flags = DeviceStateFlag(0)
    try:
      status = self._engine.master_get_uint(DarwinMasterNodeSubCommands.SAFETY_STATUS, 2.0)
    except Exception:
      return flags
    if status & 0x01:
      flags |= DeviceStateFlag.ROBOT_DISABLE
    return flags

  def is_go_button_pressed(self) -> bool:
    """Return whether the Go button flag is set in :meth:`query_state`."""
    state = self.query_state()
    return bool(state & DeviceStateFlag.GO_BUTTON)

  def clear_go_button(self) -> None:
    """Clear the latched Go-button-pressed state."""
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.CLEAR_GO_BTN_LATCH, 1, 2.0)

  # ------------------------------------------------------------------
  # Safety / recovery
  # ------------------------------------------------------------------

  def recover(self, axes: Optional[List[Axis]] = None) -> Dict[Axis, str]:
    """Recover from a safety-trip event.

    Confirms safety status is clear, then re-enables any axis whose motor
    state is DISABLED -- the state Darwin transitions axes to after a
    safety event.

    Args:
      axes: The axes to attempt recovery on. Defaults to every axis.

    Returns:
      A per-axis dict describing what action was taken: ``"enabled"`` (the
      axis was disabled, now enabled), ``"ok"`` (already enabled), or
      ``"skipped"``/``"failed: ..."`` for a transient read or enable
      failure.

    Raises:
      BravoError: If the safety interlock is still active.
    """
    if axes is None:
      axes = list(all_axes())

    if self._is_estop_engaged():
      raise BravoError(
        ErrorType.ROBOT_DISABLE,
        custom_text=(
          "Cannot recover: safety interlock still active "
          "(SAFETY_STATUS bit 0 set). Clear the light curtain / "
          "release E-stop, then retry."
        ),
      )

    result: Dict[Axis, str] = {}
    for a in axes:
      addr = axis_address(a)
      try:
        state = axis_module.read_motor_state(self._engine, addr)
      except Exception as exc:
        logger.warning("recover: read state on %s failed: %s", axis_display_name(a), exc)
        result[a] = "skipped"
        continue

      if state == MotorState.DISABLED:
        try:
          axis_module.enable(self._engine, addr, axis_display_name(a))
          result[a] = "enabled"
        except Exception as exc:
          logger.warning("recover: enable %s failed: %s", axis_display_name(a), exc)
          result[a] = f"failed: {exc}"
      else:
        result[a] = "ok"
    return result

  # ------------------------------------------------------------------
  # Lights
  # ------------------------------------------------------------------

  def set_light(self, command: LightCommandData) -> None:
    """Set the indicator light to the given color, blink period, and duty cycle."""
    encoded = _encode_light_value(command)
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.STATUS_LIGHTS, encoded, 2.0)

  def clear_lights(self) -> None:
    """Turn the indicator light off."""
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.STATUS_LIGHTS, 0, 2.0)

  # ------------------------------------------------------------------
  # Head / gripper detection
  # ------------------------------------------------------------------

  def read_head_adc(self) -> int:
    """Read the ADC-based head-count register.

    For resistor-based heads this value identifies the head. For smart
    heads the value is still readable but the smart-head EEPROM is
    authoritative.
    """
    return self._engine.master_get_uint(DarwinMasterNodeSubCommands.STUPID_HEAD_COUNTS, 2.0)

  def detect_smart_head(self) -> bool:
    """Return whether a smart head (with onboard PIC/EEPROM) is attached.

    Sends a smart-init request to the master. Success means a smart head
    responded; an ``UNSUCCESSFUL_OPERATION`` NAK means no smart head is
    present.
    """
    try:
      self._engine.master_set_uint(DarwinMasterNodeSubCommands.SMART_INIT, 0, 2.0)
      return True
    except NAKError as exc:
      if exc.nak == CommandNAKTypes.UNSUCCESSFUL_OPERATION:
        return False
      raise

  def read_smart_head_type(self) -> int:
    """Read the head-type byte from smart-head EEPROM offset 1.

    Call :meth:`detect_smart_head` first -- this raises if no smart head is
    present.
    """
    self._engine.master_set_uint(DarwinMasterNodeSubCommands.SMART_RD_EEPROM, (1 << 8) | 1, 2.0)
    value = self._engine.master_get_uint(DarwinMasterNodeSubCommands.SMART_RD_EEPROM_VAL, 2.0)
    return value & 0xFF

  def detect_head_type(self) -> HeadType:
    """Return ``"unknown"``: the firmware's head-type byte has no verified mapping.

    The EEPROM byte read from a smart head is a firmware-side encoding
    distinct from this driver's :data:`~..types.HeadType` values, and no
    verified mapping between the two exists. Returning a value derived
    directly from the byte would produce a confident but incorrect answer.
    Use :meth:`read_head_identification` for the raw byte instead.

    Returns:
      Always ``"unknown"``.
    """
    return "unknown"

  def read_head_identification(self) -> Dict[str, Any]:
    """Read raw head-identification data without interpreting it.

    Returns:
      A dict with ``"eeprom_byte"`` (the smart-head EEPROM byte, or
      ``None`` if no smart head responded), ``"adc_counts"`` (the
      resistor-based head-count register), and ``"has_smart_head"``.
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
    """Return whether the gripper accessory is currently detected.

    Presence is proven by reading the firmware version from the
    controller-tree sub-node that owns the Zg device: a successful read is
    sufficient liveness proof that the gripper sub-node is on the bus.
    """
    try:
      packed = self._engine.get_value(InstructionAddress(6), CommonSubCommands.FW_VERSION, 2.0)
    except Exception as exc:
      logger.debug("detect_gripper: FW_VERSION read failed: %s", exc)
      return GripperDetectionState.NOT_DETECTED
    return GripperDetectionState.DETECTED if packed else GripperDetectionState.NOT_DETECTED

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    """Close the gripper jaws to the given position."""
    g_addr = axis_address("g")
    g_state = self._axes["g"]
    # Validate the target before any connection work so pre-flight checks
    # catch unsafe values.
    g_state.calibration.validate_target(position, "G")
    if g_state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    self._ensure_axis_enabled("g")
    g_limits = self._limits("g")
    cal = g_state.calibration

    velocity_mm = _GRIP_SPEED_MM.get(speed, 500.0)
    # Grip current in amps: 0.3A for lids, 0.2A for plates. These feed the
    # instruction-word force_percent; I2T_PEAK_CURRENT is not written (see
    # sequences.grip -- the axis runs with firmware defaults).
    grip_current_amps = 0.3 if grip_lid else 0.2
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

  def open_gripper(self, position: Optional[float] = None) -> None:
    """Open the gripper jaws."""
    g_addr = axis_address("g")
    g_state = self._axes["g"]
    if g_state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    self._ensure_axis_enabled("g")
    cal = g_state.calibration
    limits = self._limits("g")
    target_mm = OPEN_GRIPPER_POSITION if position is None else position
    cal.validate_target(target_mm, "G")
    current_mm = self.get_position("g")
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
    """Report whether the plate-presence sensor detects a plate.

    Primary path reads the plate sensor with this controller's configured
    settle time; if that fails, falls back to an "is G away from the open
    position" heuristic so the caller always gets a bool.
    """
    try:
      return self.read_plate_sensor(transient=self._plate_sensor_transient)
    except BravoError:
      try:
        pos_mm = self.get_position("g")
        tol_mm = GRIP_POSITION_TOLERANCE / TICKS_PER_MM.get("g", 944.88)
        return abs(pos_mm - OPEN_GRIPPER_POSITION) > tol_mm
      except BravoError:
        return False

  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog on the Z or G axis."""
    axis = params.axis
    if axis not in ("z", "g"):
      raise BravoError(
        ErrorType.DARWIN_GENERIC,
        custom_text=f"jog only supported on Z and G, got {axis_display_name(axis)}",
      )
    addr = axis_address(axis)
    state = self._axes[axis]
    if state.params is None:
      raise BravoError(ErrorType.COULD_NOT_CONNECT)
    if state.peak_current_max is None:
      raise BravoError(
        ErrorType.COULD_NOT_CONNECT,
        custom_text=f"{axis_display_name(axis)} axis peak-current reference not cached; reconnect",
      )
    self._ensure_axis_enabled(axis)
    cal = state.calibration
    limits = self._limits(axis)

    def read_pos_normalized(engine: GeminiEngine, a: InstructionAddress) -> float:
      """Return the axis's current position, already normalized on the wire."""
      return engine.get_float(a, GeminiSubCommands.POSITION)

    target_normalized = cal.to_normalized(params.max_position)
    tolerance_normalized = params.tolerance / cal.hardware_range

    final_normalized = sequences.jog(
      self._engine,
      addr,
      state.params,
      sequences.JogParams(
        axis_name=axis_display_name(axis),
        target_position=target_normalized,
        tolerance=tolerance_normalized,
        peak_current_amps=params.peak_current,
        velocity_mm=params.velocity,
        acceleration_mm=params.acceleration,
        velocity_limit=limits.velocity,
        acceleration_limit=limits.acceleration,
        # The "exceeded destination" check uses a 0.05 mm epsilon near the
        # farthest point; convert to normalized axis units.
        exceed_epsilon=0.05 / cal.hardware_range,
      ),
      read_position=read_pos_normalized,
    )
    return cal.from_normalized(final_normalized)

  # ------------------------------------------------------------------
  # Plate sensor + stack scanning
  # ------------------------------------------------------------------
  #
  # Wire details:
  #   * The target device for the plate-present subcommand is the first
  #     device on the DarwinGZg node -- i.e. the G axis address (node=6,
  #     dev=0).
  #   * Enable:  SET val=2
  #     Disable: SET val=0
  #     Read:    GET -> uint; bit 0 = plate present
  #   * There is also a master-node "enable plate-presence sensor"
  #     property, but it has no effect on this firmware, so it is not
  #     used: only the G-device SET has actual wire effect.

  def _plate_sensor_enable(self, enabled: bool) -> bool:
    """Enable or disable the plate-presence sensor on the G device.

    Args:
      enabled: True to enable, False to disable.

    Returns:
      True if the write succeeded, False otherwise.
    """
    try:
      self._engine.set_uint(
        axis_address("g"), GeminiSubCommands.PLATE_PRESENT, 2 if enabled else 0, 5.0
      )
      return True
    except Exception as exc:
      logger.debug("plate-sensor enable=%s failed: %s", enabled, exc)
      return False

  def _read_plate_sensor_state(
    self,
    *,
    max_attempts: int = 1,
    retry_delay: float = 0.0,
    retry_until_present: bool = False,
  ) -> Dict[str, Any]:
    """Read the plate-sensor state, retrying per the given policy.

    Args:
      max_attempts: Maximum number of read attempts.
      retry_delay: Delay between attempts, in seconds.
      retry_until_present: Whether to keep retrying once a read succeeds
        but reports no plate present.

    Returns:
      A dict with ``"read"`` (whether any attempt succeeded), ``"present"``
      (only meaningful when ``"read"`` is True), and ``"errors"`` (a list
      of per-attempt failure descriptions).
    """
    errors: List[str] = []
    read = False
    present = False
    attempts = max(1, max_attempts)
    addr = axis_address("g")
    for i in range(attempts):
      value: Optional[int] = None
      try:
        value = self._engine.get_value(addr, GeminiSubCommands.PLATE_PRESENT, 5.0)
      except Exception as exc:
        errors.append(f"gripper_sensor_read={exc}")
      if value is not None:
        present = bool(value & 1)
        read = True
        if present or not retry_until_present:
          break
      if retry_delay > 0 and i < attempts - 1:
        time.sleep(retry_delay)
    return {"read": read, "present": present, "errors": errors}

  def read_plate_sensor(self, transient: float = 0.0) -> bool:
    """Enable the plate sensor, wait ``transient``, read, disable.

    Args:
      transient: How long to allow a transient sensor reading to settle,
        in seconds, before treating it as final.

    Returns:
      True if a plate is detected.

    Raises:
      BravoError: If every attempt to read the sensor failed -- an
        unreadable sensor must never be reported as "no plate".
    """
    self._plate_sensor_enable(True)
    try:
      if transient > 0:
        time.sleep(transient)
      result = self._read_plate_sensor_state(
        max_attempts=3, retry_delay=0.1, retry_until_present=True
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
    transient: float = 0.0,
  ) -> Dict[str, Any]:
    """Scan the Zg axis between two heights until the plate sensor detects a stack top.

    Behavior:

      1. Move Zg to ``start_zg`` (absolute).
      2. Enable the plate sensor, optionally sleep ``transient``.
      3. Initial read with 3 attempts / 100 ms delay / retry-until-present --
         if nothing reads, raise.
      4. If a plate is already detected at the start, back off upward in
         10 mm steps until the sensor clears (or Zg reaches -20 mm).
      5. Descend stepwise toward ``end_zg``; after each step, poll the
         sensor with 3 attempts / 10 ms delay / retry-until-present. The
         first "present" hit terminates with ``detected=True``. Reaching
         ``end_zg`` without a hit returns ``detected=False``.
      6. Always disable the plate sensor in a ``finally`` block.

    Speed-dependent step size: fast=1.0, slow=0.25, else 0.5 mm. Velocity:
    fast=20, slow=2, else 5 mm/s. Acceleration: min(80, axis acceleration
    limit) mm/s^2.

    Args:
      start_zg: Zg position to start the scan from, in mm.
      end_zg: Zg position to stop the scan at if nothing is detected, in
        mm.
      speed: The speed profile to scan at.
      transient: How long to allow a transient sensor reading to settle,
        in seconds, before treating it as final.

    Returns:
      A dict with ``"detected"`` (bool), ``"scan_mode"`` (str),
      ``"elapsed_ms"``, ``"poll_count"``, ``"sensor_reads"``,
      ``"sensor_read_failures"`` (ints), ``"positions"`` (per-axis mm), and
      ``"telemetry"`` (per-axis diagnostics).

    Raises:
      BravoError: If the plate sensor could never be read.
    """
    self._ensure_axis_enabled("zg")

    if speed == "fast":
      velocity_mm = 20.0
      step_mm = 1.0
    elif speed == "slow":
      velocity_mm = 2.0
      step_mm = 0.25
    else:
      velocity_mm = 5.0
      step_mm = 0.5

    zg_limits = self._limits("zg")
    accel_mm = min(80.0, zg_limits.acceleration if zg_limits.acceleration > 0 else 40.0)
    if accel_mm <= 0.0:
      accel_mm = 40.0

    sensor_read_count = 0
    sensor_read_failures = 0
    sensor_read_errors: List[str] = []
    poll_count = 0
    detected = False
    scan_started_at = time.monotonic()

    def _zg_move(target: float) -> None:
      """Move Zg to ``target`` absolute, with a distance-scaled timeout."""
      distance = abs(target - self.get_position("zg"))
      move_timeout = _motion_timeout(distance, velocity_mm, min_s=4.0, margin_s=1.0)
      self.move(
        [
          AxisMoveInfo(
            axis="zg", position=target, velocity=velocity_mm, acceleration=accel_mm, absolute=True
          )
        ],
        wait=True,
        timeout=move_timeout,
      )

    # Step 1: seek to start_zg.
    start_distance = abs(start_zg - self.get_position("zg"))
    start_timeout = _motion_timeout(start_distance, velocity_mm, min_s=6.0, margin_s=2.0)
    self.move(
      [
        AxisMoveInfo(
          axis="zg", position=start_zg, velocity=velocity_mm, acceleration=accel_mm, absolute=True
        )
      ],
      wait=True,
      timeout=start_timeout,
    )

    # Step 2+: enable sensor, then scan.
    self._plate_sensor_enable(True)
    try:
      if transient > 0:
        time.sleep(transient)

      initial = self._read_plate_sensor_state(
        max_attempts=3, retry_delay=0.1, retry_until_present=True
      )
      if not initial["read"]:
        detail = "; errors=" + " | ".join(initial["errors"]) if initial["errors"] else ""
        raise BravoError(
          ErrorType.COULD_NOT_QUERY_STATE,
          custom_text=f"Could not read plate sensor state from Darwin during scan{detail}",
        )
      sensor_read_count += 1
      present = bool(initial["present"])

      # Step 4: already on a plate? Back off upward in 10 mm chunks.
      while present and self.get_position("zg") > -20.0:
        target = max(-20.0, self.get_position("zg") - 10.0)
        _zg_move(target)
        back = self._read_plate_sensor_state(max_attempts=3, retry_delay=0.01)
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
      while self.get_position("zg") < end_zg:
        target = min(end_zg, self.get_position("zg") + step_mm)
        _zg_move(target)
        poll_count += 1
        step_read = self._read_plate_sensor_state(
          max_attempts=3, retry_delay=0.01, retry_until_present=True
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
        custom_text=f"Could not read plate sensor state from Darwin master during scan{detail}",
      )

    elapsed_ms = int((time.monotonic() - scan_started_at) * 1000)
    self._last_snapshot = None  # Positions changed.
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

  def get_all_positions(self) -> Dict[str, float]:
    """Return the current position of every axis, in mm.

    A naive per-axis loop -- the Gemini protocol has no multipacket read
    for position queries.
    """
    out: Dict[str, float] = {}
    for a in ("x", "y", "z", "w", "g", "zg"):
      try:
        out[axis_display_name(a)] = float(self.get_position(a))
      except Exception as exc:
        logger.debug("get_all_positions: %s read failed: %s", axis_display_name(a), exc)
    return out

  def _motor_states(self) -> Dict[str, bool]:
    """Return each axis's enabled flag, keyed by display name."""
    out: Dict[str, bool] = {}
    for a in ("x", "y", "z", "w", "g", "zg"):
      try:
        out[axis_display_name(a)] = bool(self.is_motor_enabled(a))
      except Exception:
        out[axis_display_name(a)] = False
    return out

  def _state_flags(self) -> int:
    """Return a state bitfield: 0x01=E-stop, 0x02=motor power (any axis enabled).

    The Go-button bit stays 0: there is no verified on-wire subcommand
    mapping for a live Go-button read, and the Go button is an operator-
    advance input rather than a motion input.
    """
    flags = 0
    if self._is_estop_engaged():
      flags |= 0x01
    if any(self._motor_states().values()):
      flags |= 0x02
    return flags

  def _axis_telemetry(self) -> Dict[str, Dict[str, Any]]:
    """Return per-axis diagnostics: position, enabled, limits, calibration, last command.

    Fields not cheaply observable without an extra round trip (measured
    current, peak current beyond the cached reference, position error) are
    omitted rather than faked.
    """
    telem: Dict[str, Dict[str, Any]] = {}
    for a in ("x", "y", "z", "w", "g", "zg"):
      state = self._axes.get(a)
      if state is None:
        continue
      cal = state.calibration
      entry: Dict[str, Any] = {
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
      telem[axis_display_name(a)] = entry
    return telem

  def get_state_snapshot(self, max_age_s: float = 0.15) -> Dict[str, Any]:
    """Return a composite snapshot: positions, motor states, flags, head/gripper, telemetry.

    Cached for ``max_age_s`` seconds so rapid callers do not hammer the
    wire.

    Args:
      max_age_s: How long a cached snapshot remains valid, in seconds.

    Returns:
      A dict with ``"positions"``, ``"motors_enabled"``,
      ``"head_attached"``, ``"gripper_present"``, ``"go_button_pressed"``,
      ``"robot_disabled"``, and ``"telemetry"``.
    """
    now = time.monotonic()
    if self._last_snapshot is not None and (now - self._last_snapshot_at) <= max_age_s:
      return dict(self._last_snapshot)

    positions = self.get_all_positions()
    motors = self._motor_states()
    flags = self._state_flags()

    head_attached = False
    try:
      head_attached = bool(self.detect_smart_head())
    except Exception:
      head_attached = self._head_type != "unknown"
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
  # Send command (generic dispatch)
  # ------------------------------------------------------------------

  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    """Map a legacy command ID to its native Darwin equivalent, where one exists.

    Darwin has no generic command dispatch -- the few legacy command IDs
    that higher-level code still issues are mapped to either a no-op or an
    equivalent native method.

    Args:
      command_id: The legacy command ID.
      data: The command payload; unused.
      timeout: Unused; kept for a uniform interface signature.

    Returns:
      An empty payload for every handled command.

    Raises:
      BravoError: If ``command_id`` has no Darwin equivalent.
    """
    del data, timeout
    if command_id == CommandID.CLEAR_MOTOR_POWER_FAULT:
      # Darwin firmware has no motor-power-fault concept reachable over
      # Gemini, so there is nothing to clear.
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
      custom_text=f"Darwin command passthrough is not implemented for 0x{int(command_id):02X}.",
    )

  # ------------------------------------------------------------------
  # Error tracking
  # ------------------------------------------------------------------

  def _set_error(self, error: BravoError) -> None:
    """Record the most recent error and log it.

    Args:
      error: The error to record.
    """
    self._last_error = error
    logger.error("Darwin error: %s", error)

  @property
  def last_error(self) -> Optional[BravoError]:
    """The most recent error this controller recorded, if any."""
    return self._last_error

  # ------------------------------------------------------------------
  # Head-type management
  # ------------------------------------------------------------------

  def set_head_type(self, head_type: HeadType) -> None:
    """Declare the currently-attached pipette head.

    Updates the W-axis hardware range and uL-to-mm factor, and marks the
    57-parameter W-axis table for re-apply on the next W move. Must be
    called before any aspirate/dispense so the plunger positions are
    interpreted correctly.

    Args:
      head_type: The head type now installed.
    """
    self._head_type = head_type
    self._waxis_applied_head = None  # Force a param re-apply on the next W move.
    cfg = config_for_head(head_type)
    if cfg is not None:
      self._axes["w"].calibration = cfg.calibration()
      self._axes["w"].limits = None  # Hardware range changed; cached limits are stale.

  def get_head_type(self) -> HeadType:
    """Return the head type most recently set with :meth:`set_head_type`."""
    return self._head_type

  def ul_to_mm(self, volume_ul: float) -> float:
    """Convert a pipette volume in microliters to W-axis mm for the current head."""
    return ul_to_mm(volume_ul, self._head_type)

  # ------------------------------------------------------------------
  # W-axis pipetting (aspirate / dispense) -- convenience wrappers on move()
  # ------------------------------------------------------------------

  def aspirate(
    self,
    volume_ul: float,
    *,
    velocity_mm: float = 50.0,
    acceleration_mm: float = 500.0,
    timeout: float = 15.0,
  ) -> None:
    """Draw liquid by extending the plunger ``volume_ul`` above park.

    Positions the W axis at ``+volume_ul * factor`` mm from park. Requires
    :meth:`set_head_type` to have been called so the uL-to-mm factor is
    known.

    Args:
      volume_ul: The volume to draw, in microliters.
      velocity_mm: Move velocity, in mm/s.
      acceleration_mm: Move acceleration, in mm/s^2.
      timeout: Maximum time to wait for the move to finish, in seconds.

    Raises:
      BravoError: If no head type has been set.
    """
    if self._head_type == "unknown":
      raise BravoError(
        ErrorType.DARWIN_GENERIC,
        custom_text="aspirate requires set_head_type() first",
      )
    target_mm = self.ul_to_mm(volume_ul)
    self.move(
      [
        AxisMoveInfo(
          axis="w",
          position=target_mm,
          velocity=velocity_mm,
          acceleration=acceleration_mm,
          absolute=True,
        )
      ],
      wait=True,
      timeout=timeout,
    )

  def dispense(
    self,
    volume_ul: float,
    *,
    velocity_mm: float = 50.0,
    acceleration_mm: float = 500.0,
    timeout: float = 15.0,
  ) -> None:
    """Expel liquid by driving the plunger toward its ``volume_ul`` position.

    Moves W from its current position toward the position corresponding to
    ``volume_ul`` (0 for park). To dispense a specific volume, call
    :meth:`aspirate` first to set the starting position, then
    :meth:`dispense` with 0 or a smaller volume to leave residual.

    Args:
      volume_ul: The plunger target, in microliters.
      velocity_mm: Move velocity, in mm/s.
      acceleration_mm: Move acceleration, in mm/s^2.
      timeout: Maximum time to wait for the move to finish, in seconds.
    """
    target_mm = self.ul_to_mm(volume_ul)
    self.move(
      [
        AxisMoveInfo(
          axis="w",
          position=target_mm,
          velocity=velocity_mm,
          acceleration=acceleration_mm,
          absolute=True,
        )
      ],
      wait=True,
      timeout=timeout,
    )


# ----------------------------------------------------------------------------
# Light-encoding helper
# ----------------------------------------------------------------------------


def _encode_light_value(command: LightCommandData) -> int:
  """Encode a light command into the native Darwin status-light word.

  Args:
    command: The light command to encode.

  Returns:
    The packed 32-bit status-light value.
  """
  colors = int(command.light)
  blue = 100 if (colors & 0x08) else 0
  red = 0
  green = 0
  low_bits = colors & 0x07
  if low_bits == 1:
    red = 100
  elif low_bits == 2:
    red = 25
    green = 100
  elif low_bits == 3:
    red = 100
    green = 65
  elif low_bits == 4:
    green = 100
  elif low_bits == 5:
    red = 100
    green = 100
  elif low_bits == 6:
    red = 20
    green = 100
  elif low_bits == 7:
    red = 80
    green = 100
  period = int(command.period_ms or 0)
  duty = float(command.duty_cycle or 0.0)
  if duty == 1.0 or period > 2000:
    blink_rate = 0
  elif 0.7 < duty < 0.9:
    blink_rate = int(period / 20) | 0x80
  else:
    blink_rate = int((period + 20) / 40) & 0x7F
  return ((red & 0xFF) << 24) | ((green & 0xFF) << 16) | ((blue & 0xFF) << 8) | (blink_rate & 0xFF)
