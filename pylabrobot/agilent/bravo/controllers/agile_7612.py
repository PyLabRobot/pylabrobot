"""Agile 7612 controller for Bravo hardware with 7612-generation wire encoding.

Speaks the same Agile V11 protocol as :mod:`.agile`, with generation-specific
differences:

- V11 frame byte order: ``[cmd][length]`` instead of ``[length][cmd]``.
- CRC-8/MAXIM instead of CRC-8/SMBUS.
- The move-command payload packs ``home_complete_register`` as a 16-bit
  value instead of 32-bit.
- No ``move_go`` / ``servo_enable`` / ``get_group_a_status`` Agile commands;
  motion is triggered and polled through header/subtype byte sequences
  instead.
- Two-phase, host-driven homing with per-axis servo configuration.
- Force-controlled jog via ``CMD_PREPARE_JOG`` plus a 0x80-header trigger.
- Servo write header is ``local_axis_index * 0x10``, not a fixed value.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Optional, Union, cast

from ..axis_config import AxisConfig, default_axis_config
from ..errors import BravoError, ErrorType
from ..protocol import agile_7612_packet
from ..protocol.agile_7612_commands import Agile7612MoveInfo
from ..protocol.agile_7612_crc import crc8_maxim
from ..protocol.agile_packet import AGILE_PACKET_SIZE
from ..protocol.commands import CommandID
from ..protocol.v11_agile_7612_comm import V11Agile7612DeviceComm
from ..transport import Transport
from ..types import (
  ALL_AXES,
  Axis,
  DeviceStateFlag,
  GripperDetectionState,
  HeadType,
  SpeedLevel,
  axis_code,
  axis_display_name,
  axis_label,
)
from .agile import (
  _CONTROLLER_1_AXES,
  _CONTROLLER_2_AXES,
  _CONTROLLER_2_ID,
  AgileController,
  _axis_bit,
  _local_axis_index,
)
from .base import AxisMoveInfo, JogParams

logger = logging.getLogger(__name__)

_HOMING_DISTANCE_MM = 10_000.0
_STOP_RETRIES = 3
_STOP_RETRY_DELAY = 0.200  # seconds

# Firmware park position (in mm from the home sensor) for each axis. Every
# axis parks at firmware 0 (the home sensor) except Zg, which parks at
# -20mm (its nesting/docking position, hardcoded in ``_home_zg``).
_FIRMWARE_PARK_MM: dict[Axis, float] = {"zg": -20.0}

# Per-axis servo register 0xA0 values used during homing.
_HOMING_SERVO_REG_A0: dict[Axis, bytes] = {
  "x": bytes.fromhex("60c1762bfd1000"),
  "y": bytes.fromhex("60c1762bfd1000"),
  "z": bytes.fromhex("7ae147aeff1000"),
  "w": bytes.fromhex("7ae147aeff1000"),  # assumed same as Z
  "g": bytes.fromhex("489122ebff1000"),
  "zg": bytes.fromhex("78f1e7d5fe1000"),
}

# Servo register values: initial, between-phase swap, and post-phase reset.
_SERVO_A3_INITIAL = bytes.fromhex("40000000011000")
_SERVO_A4_INITIAL = bytes.fromhex("00000000001000")
_SERVO_A3_SWAPPED = bytes.fromhex("00000000001000")  # A3 gets A4's initial value
_SERVO_A4_SWAPPED = bytes.fromhex("40000000011000")  # A4 gets A3's initial value
_SERVO_A4_RESET = bytes.fromhex("00000000001000")  # A4 reset after phase 2

# Home register enable/update values.
_HOME_REG_ENABLE = bytes.fromhex("00000000001000")
_HOME_REG_HOMED = bytes.fromhex("40000000011000")

# Fallback sensor-flag bitmask per axis, used when an axis's configuration
# leaves home_flag_bitmask at its zero default. Each Controller 1 axis has
# its own bit (X=0x01, Y=0x02, Z=0x04, W=0x08); Controller 2 axes reuse
# 0x01/0x02 (G, Zg).
_DEFAULT_HOME_SENSOR_BITMASK: dict[Axis, int] = {"x": 1, "y": 2, "z": 4, "w": 8, "g": 1, "zg": 2}


def _homing_servo_registers(axis: Axis) -> list[tuple[int, bytes]]:
  """Build the per-axis servo register writes used to prepare an axis for homing.

  Args:
    axis: The axis to build servo register values for.

  Returns:
    A list of ``(register, data)`` pairs to write in order.
  """
  local_idx = _local_axis_index(axis)
  axis_byte = local_idx + 1
  reg_a0 = _HOMING_SERVO_REG_A0.get(axis, bytes.fromhex("7ae147aeff1000"))
  ae_data = bytearray.fromhex("40000000001000")
  ae_data[4] = axis_byte
  b0_data = bytearray.fromhex("40000000001000")
  b0_data[4] = axis_byte
  return [
    (0xA0, reg_a0),
    (0xAD, bytes.fromhex("488000000c1000")),
    (0xAE, bytes(ae_data)),
    (0xAF, bytes.fromhex("00000000001000")),
    (0xB0, bytes(b0_data)),
    (0xBD, bytes.fromhex("00000000001000")),
  ]


def _home_reg_register(axis: Axis) -> int:
  """Return the Agile register number for an axis's home-complete register.

  X/G use 0x5E, Y/Zg use 0x5F, Z uses 0x60, W uses 0x61.

  Args:
    axis: The axis to look up.

  Returns:
    The register address.
  """
  mapping = {0: 0x5E, 1: 0x5F, 2: 0x60, 3: 0x61, 4: 0x5E, 5: 0x5F}
  return mapping.get(axis_code(axis), 0x5E)


class Agile7612Controller(AgileController):
  """Agile controller for Agile 7612-generation Bravo hardware.

  Attributes:
    has_gripper: Whether this model has a gripper accessory.
    model_name: The human-readable model name, used in diagnostic messages.
  """

  _comm_cls = V11Agile7612DeviceComm

  has_gripper = True
  model_name = "Bravo 7612"

  def __init__(
    self,
    transport: Transport,
    axis_config: Optional[dict[Axis, AxisConfig]] = None,
  ) -> None:
    """Bind this controller to an already-connected transport.

    Args:
      transport: The transport to communicate over. The caller owns its
        connection lifecycle.
      axis_config: Per-axis motion configuration, keyed by axis. An axis
        missing from this mapping falls back to
        :func:`~pylabrobot.agilent.bravo.axis_config.default_axis_config`.
        Every axis's :attr:`~pylabrobot.agilent.bravo.axis_config.AxisConfig.ticks_per_eng_unit`
        (whether from the given mapping or the default) becomes this
        controller's encoder scale for that axis.
    """
    super().__init__(transport)
    self._agile_pkt = agile_7612_packet
    self._move_info_cls = Agile7612MoveInfo
    self._head_type: HeadType = "unknown"

    provided = axis_config or {}
    self._axis_config: dict[Axis, AxisConfig] = {
      axis: provided.get(axis, default_axis_config(axis)) for axis in ALL_AXES
    }
    for axis, cfg in self._axis_config.items():
      self._ticks_per_unit[axis] = cfg.ticks_per_eng_unit

    self._home_raw: dict[Axis, float] = {}
    self._tracked_position: dict[Axis, float] = {}

  def initialize(self) -> None:
    """Perform the base handshake, then clear this generation's tracked motion state.

    ``_home_raw`` and ``_tracked_position`` are only valid for the
    connection that produced them: once initialize() runs again (a fresh
    connect, or a reconnect on the same instance), any position they
    recorded is no longer trustworthy. Cleared here alongside the homed
    state the base class resets, rather than left to accumulate stale
    entries across reconnects.
    """
    super().initialize()
    self._home_raw.clear()
    self._tracked_position.clear()

  # =================================================================
  # Connection & verification
  # =================================================================

  _AGILE_7612_VERIFY_HEADER = 0x09
  _AGILE_7612_VERIFY_REGISTER = 0x90
  _AGILE_7612_UNIQUE_VALUE = 0x2A55

  def _verify_controller(self, controller_id: int) -> bool:
    """Confirm an Agile 7612 controller is alive by reading its unique-value register.

    Args:
      controller_id: The Agile bus controller ID to verify.

    Returns:
      True if the controller responds with the expected unique value.
    """
    raw = bytearray(AGILE_PACKET_SIZE)
    raw[0] = self._AGILE_7612_VERIFY_HEADER
    raw[1] = self._AGILE_7612_VERIFY_REGISTER
    raw[9] = crc8_maxim(raw, 9)
    axis_index = 4 if controller_id == _CONTROLLER_2_ID else 0
    try:
      comm = self._require_connected()
      payload = bytes(raw) + struct.pack("<B", axis_index)
      response = comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)
      if len(response) < AGILE_PACKET_SIZE:
        return False
      value = struct.unpack_from("<H", response, 2)[0]
      if value != self._AGILE_7612_UNIQUE_VALUE:
        logger.error("Controller %d unique-value mismatch: 0x%04X", controller_id, value)
        return False
      logger.info("Controller %d verified (0x%04X)", controller_id, value)
      return True
    except BravoError as exc:
      logger.error("Controller %d verification failed: %s", controller_id, exc)
      return False

  def get_diagnostics(self) -> dict[str, object]:
    """Return a snapshot of this controller's comm-layer diagnostics.

    Returns:
      A dict with ``connected``, and when connected, ``command_counts``
      (commands sent, by name), ``errors`` (the comm layer's error log),
      and ``error_count``.
    """
    if not self._comm.is_connected:
      return {"connected": False}
    # self._comm is always a V11Agile7612DeviceComm for this class -- set
    # from _comm_cls in AgileController.__init__ -- so this narrows the
    # inherited V11DeviceComm type rather than checking anything at runtime.
    comm = cast(V11Agile7612DeviceComm, self._comm)
    return {
      "connected": True,
      "command_counts": dict(comm.command_counts),
      "errors": list(comm.error_log),
      "error_count": len(comm.error_log),
    }

  # =================================================================
  # STOP command
  # =================================================================

  def stop(self) -> None:
    """Send ``CMD_STOP``, retrying if it is not acknowledged."""
    comm = self._require_connected()
    for attempt in range(1, _STOP_RETRIES + 1):
      try:
        comm.send_command(CommandID.STOP, timeout=1.0)
        logger.info("STOP acknowledged on attempt %d", attempt)
        return
      except (BravoError, TimeoutError):
        if attempt < _STOP_RETRIES:
          time.sleep(_STOP_RETRY_DELAY)
    logger.warning("STOP not acknowledged after %d attempts", _STOP_RETRIES)

  # =================================================================
  # Agile packet helpers
  # =================================================================

  def _send_agile(self, packet: bytes, axis: Optional[Axis] = None, timeout: float = 2.0) -> bytes:
    """Send a 10-byte Agile packet via ``CMD_DIRECT_AGILE_COMMAND``.

    Unlike the base class, this always appends a trailing axis-index byte
    -- there is no firmware-version gate on the Agile 7612 generation, only
    the legacy generation's firmware 2.0.0+ requirement. When no axis is
    given, the index is inferred from the packet's own controller-id byte
    (byte 1) rather than left off.

    Args:
      packet: The 10-byte Agile packet to send.
      axis: The axis this packet targets, if any. Falls back to inferring
        the controller from ``packet[1]`` when omitted.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The raw response payload.
    """
    comm = self._require_connected()
    if axis is not None:
      axis_index = axis_code(axis)
    else:
      cid = packet[1] if len(packet) > 1 else 0
      axis_index = 4 if cid == 1 else 0
    payload = packet + struct.pack("<B", axis_index)
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload, timeout)

  def _agile_7612_agile_read(self, register: int, axis: Axis) -> bytes:
    """Read a register with the standard per-axis header (``0x01 + local_idx * 0x10``)."""
    local_idx = _local_axis_index(axis)
    header = 0x01 + (local_idx * 0x10)
    raw = bytearray(10)
    raw[0] = header
    raw[1] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    return self._send_agile(bytes(raw), axis)

  def _agile_7612_ext_read(self, register: int, axis: Axis) -> bytes:
    """Read a register with the extended header (``0x09``)."""
    raw = bytearray(10)
    raw[0] = 0x09
    raw[1] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    return self._send_agile(bytes(raw), axis)

  def _agile_7612_status_read(self, register: int, axis_index: int) -> bytes:
    """Read controller status. The register goes in byte 7, not byte 1."""
    raw = bytearray(10)
    raw[0] = 0x00
    raw[7] = register & 0xFF
    raw[9] = crc8_maxim(raw, 9)
    comm = self._require_connected()
    payload = bytes(raw) + struct.pack("<B", axis_index)
    return comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)

  def _agile_7612_servo_write(self, register: int, data: bytes, axis: Axis) -> None:
    """Write a servo register. The header is ``local_axis_index * 0x10``."""
    header = _local_axis_index(axis) * 0x10
    raw = bytearray(10)
    raw[0] = header
    raw[1] = register & 0xFF
    for i, b in enumerate(data[:6]):
      raw[2 + i] = b
    raw[8] = data[6] if len(data) > 6 else 0
    raw[9] = crc8_maxim(raw, 9)
    self._send_agile(bytes(raw), axis)

  def _agile_7612_write_home_reg(self, axis: Axis, data: bytes) -> None:
    """Write an axis's home-complete register.

    The firmware expects header ``0x01`` (not the servo write header) for
    this register, targeting the Agile register address for this axis
    (X/G=0x5E, Y/Zg=0x5F, Z=0x60, W=0x61) -- not the PREPARE_MOVE payload's
    home_complete_register field, which is a different value entirely (an
    AxisConfig field defaulting to 0 unless a caller overrides it).
    """
    reg = _home_reg_register(axis)
    raw = bytearray(10)
    raw[0] = 0x01
    raw[1] = reg & 0xFF
    for i, b in enumerate(data[:6]):
      raw[2 + i] = b
    raw[8] = data[6] if len(data) > 6 else 0
    raw[9] = crc8_maxim(raw, 9)
    self._send_agile(bytes(raw), axis)

  def _agile_7612_fault_reset_ctrl2(self) -> None:
    """Reset controller 2's fault state after a move, as the firmware expects."""
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = 0x01
    raw[7] = 0x31
    raw[9] = crc8_maxim(raw, 9)
    try:
      comm = self._require_connected()
      payload = bytes(raw) + struct.pack("<B", 4)
      comm.send_command(CommandID.DIRECT_AGILE_COMMAND, payload)
    except BravoError:
      self._drain_tcp_buffer()

  def _drain_tcp_buffer(self) -> None:
    """Discard stale bytes left in the transport after a comm error."""
    self._comm.transport.drain()

  # =================================================================
  # Unsupported commands
  # =================================================================

  _UNSUPPORTED_COMMANDS = frozenset(
    {
      CommandID.CLEAR_MOTOR_POWER_FAULT,
      CommandID.QUERY_MOTOR_POWER,
      CommandID.GET_POSITION,
      CommandID.DETECT_SMART_HEAD,
      CommandID.READ_AD_WEIGH_PAD,
    }
  )

  def send_command(self, command_id: int, data: bytes = b"", timeout: float = 2.0) -> bytes:
    """Send a raw V11 command, silently no-op-ing commands this generation does not support.

    Unlike the base class, a command in :attr:`_UNSUPPORTED_COMMANDS`
    (register queries the Agile 7612 firmware does not implement) returns
    ``b""`` instead of being sent -- the base class's contract of sending
    whatever it is given and surfacing the device's own response or error
    does not hold here for those specific commands.

    Args:
      command_id: The command to send.
      data: The command's payload bytes, if any.
      timeout: Maximum time to wait for the response, in seconds.

    Returns:
      The response payload, or ``b""`` for an unsupported command.
    """
    cid = CommandID(command_id) if isinstance(command_id, int) else command_id
    if cid in self._UNSUPPORTED_COMMANDS:
      logger.debug("Agile7612: skipping unsupported command 0x%02X", command_id)
      return b""
    return super().send_command(command_id, data, timeout)

  # =================================================================
  # Position reading
  # =================================================================

  _CTRL2_EFFECTIVE_TPU: dict[Axis, float] = {
    "g": 126.8 * (944.882 / 787.402),
    "zg": 126.8,
  }

  # Position register resolution multiplier per axis: the register changes
  # by (ticks_sent x multiplier) for each move. X/Y are 16x, Z is 8x; W is
  # not yet independently measured and uses the same 8x as Z until it is.
  _CTRL1_POSITION_SCALE: dict[Axis, float] = {
    "x": 16.0,
    "y": 16.0,
    "z": 8.0,
    "w": 8.0,
  }

  def _read_raw_position(self, axis: Axis) -> float:
    """Read the raw position register and convert it to engineering units.

    Args:
      axis: The axis to read.

    Returns:
      The position, in mm (or uL for the W axis), relative to firmware
      zero.

    Raises:
      BravoError: If the register read returns too little data.
    """
    response = self._agile_7612_agile_read(0x07, axis)
    if len(response) < 10:
      raise BravoError(ErrorType.COULD_NOT_READ_POSITION, axis=axis)
    raw_be_u16 = struct.unpack_from(">H", response, 2)[0]
    if axis in _CONTROLLER_1_AXES:
      scale = self._CTRL1_POSITION_SCALE.get(axis, 8.0)
      tpu = self._ticks_per_unit.get(axis, 314.96)
      return float(raw_be_u16) / (tpu * scale / 2.0)
    sign = -1.0 if (raw_be_u16 & 0x8000) else 1.0
    magnitude = raw_be_u16 & 0x7FFF
    eff_tpu = self._CTRL2_EFFECTIVE_TPU.get(axis, 126.8)
    return sign * float(magnitude) * 2.0 / eff_tpu

  def get_position(self, axis: Axis) -> float:
    """Return the current position of an axis, in engineering units (mm or uL).

    Unlike the base class, this does not necessarily read the device: the
    last position :meth:`move` (or homing) computed for this axis is
    trusted and returned directly when available, since this generation's
    position registers wrap and cannot always be decoded back to an
    absolute engineering-unit value on their own. Only when nothing is
    tracked yet does this fall back to reading the raw position register,
    which it then offsets against the raw reading captured at the last
    home.

    Args:
      axis: The axis to read.

    Returns:
      The tracked position if one is known, otherwise the raw register
      reading (offset against the last home, if the axis has one).
    """
    if axis in self._tracked_position:
      return self._tracked_position[axis]
    raw = self._read_raw_position(axis)
    if axis in self._home_raw:
      home_offset = self.get_park_position(axis)
      return (raw - self._home_raw[axis]) + home_offset
    return raw

  def get_all_positions(self) -> dict[str, float]:
    """Return every axis's position, keyed by its display name.

    Returns:
      A dict from axis display name (e.g. ``"Zg"``) to position, in mm (or
      uL for W). An axis whose read fails is omitted rather than raising.
    """
    out: dict[str, float] = {}
    for axis in ALL_AXES:
      try:
        out[axis_display_name(axis)] = self.get_position(axis)
      except Exception as exc:  # noqa: BLE001 - a single axis read must not abort the rest
        logger.debug("get_all_positions: %s read failed: %s", axis_display_name(axis), exc)
    return out

  def _capture_home_position(self, axis: Axis) -> None:
    """Record the tracked and raw positions once an axis finishes homing.

    Args:
      axis: The axis that just finished homing.
    """
    park = self.get_park_position(axis)
    self._tracked_position[axis] = park
    try:
      self._home_raw[axis] = self._read_raw_position(axis)
    except BravoError:
      pass
    logger.info("Home position %s: tracked=%.3f", axis_label(axis), park)

  # =================================================================
  # Motion -- PREPARE_MOVE + trigger
  # =================================================================

  _MOVE_POLL_INTERVAL = 0.050
  _STATUS_REG_GENERAL = 0x90
  _STATUS_SETTLED = 0xB0
  _TRIGGER_SUBTYPE = 0x38
  _JOG_TRIGGER_HEADER = 0x80
  _JOG_TRIGGER_SUBTYPE = 0x36

  def _home_reg_for_axis(self, axis: Axis) -> int:
    """Return the axis's configured home-complete register."""
    return self._axis_config[axis].home_complete_register

  def _agile_7612_move_go(self, axes: list[Axis]) -> None:
    """Trigger pending moves, one axis at a time.

    Each trigger is header=0x00, byte[1]=axis bitmask, byte[7]=0x38,
    routed by the axis's own wire code -- not combined into one bitmasked
    command for every axis at once.

    Args:
      axes: The axes whose pending moves should start.
    """
    comm = self._require_connected()
    for axis in axes:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = self._TRIGGER_SUBTYPE
      raw[9] = crc8_maxim(raw, 9)
      comm.send_command(
        CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", axis_code(axis))
      )

  def _agile_7612_jog_trigger(self, axis: Axis) -> None:
    """Trigger a force-controlled jog: header=0x80, byte[7]=0x36.

    Args:
      axis: The axis to trigger the jog on.
    """
    raw = bytearray(10)
    raw[0] = self._JOG_TRIGGER_HEADER
    raw[2] = 0x40
    raw[6] = 0x05
    raw[7] = self._JOG_TRIGGER_SUBTYPE
    raw[9] = crc8_maxim(raw, 9)
    comm = self._require_connected()
    comm.send_command(
      CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", axis_code(axis))
    )

  def _speed_for_level(self, axis: Axis, level: SpeedLevel) -> tuple[float, float]:
    """Return the velocity/acceleration pair configured for an axis and speed level.

    Args:
      axis: The axis to look up.
      level: The speed level to look up.

    Returns:
      A ``(velocity, acceleration)`` pair. Falls back to ``(50.0, 100.0)``
      if the axis's configuration has no entry for ``level``.
    """
    profile = self._axis_config[axis].speeds.get(level)
    if profile is not None:
      return (profile.velocity, profile.acceleration)
    return (50.0, 100.0)

  def _default_vel_accel(self, axis: Axis) -> tuple[float, float]:
    """Return the axis's "safe" speed level, used when a move requests no velocity."""
    return self._speed_for_level(axis, "safe")

  def move(self, moves: list[AxisMoveInfo], wait: bool = True, timeout: float = 30.0) -> None:
    """Execute motion via ``CMD_PREPARE_MOVE`` plus a per-axis trigger.

    Args:
      moves: The per-axis targets to move to together.
      wait: Whether to block until the move finishes.
      timeout: Maximum time to wait for the move to finish, in seconds.

    Raises:
      BravoError: If any targeted axis has not been homed, or a target
        falls outside the axis's configured range.
    """
    if not moves:
      return
    for m in moves:
      if not self._homed[m.axis]:
        raise BravoError(
          ErrorType.COULD_NOT_MOVE_TO_POSITION,
          custom_text=(
            f"{axis_display_name(m.axis)} axis not initialized; "
            "home the axis before issuing a move."
          ),
        )
    comm = self._require_connected()
    for m in moves:
      self._validate_target(m)
    for m in moves:
      vel = m.velocity
      accel = m.acceleration
      if vel == 0.0:
        vel, accel = self._default_vel_accel(m.axis)
      if m.absolute:
        origin = self._move_origin(m.axis)
        firmware_mm = m.position - origin
      else:
        firmware_mm = m.position
      info = self._move_info_cls(
        axis=m.axis,
        position=self._to_ticks(m.axis, firmware_mm),
        velocity=self._vel_to_ticks_per_ms(m.axis, vel),
        acceleration=self._accel_to_ticks_per_ms2(m.axis, accel),
        absolute_move=m.absolute,
        check_for_homed=True,
        home_complete_register=self._home_reg_for_axis(m.axis),
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([m.axis for m in moves])
    if wait:
      self._agile_7612_wait_for_settled([m.axis for m in moves], timeout)
    for m in moves:
      if m.absolute:
        self._tracked_position[m.axis] = m.position
      elif m.axis in self._tracked_position:
        self._tracked_position[m.axis] += m.position
    self._agile_7612_fault_reset_ctrl2()

  def _move_origin(self, axis: Axis) -> float:
    """Return the engineering-unit position corresponding to firmware zero ticks.

    After homing, firmware 0 is the home sensor. The engineering position
    there is the axis's homing offset minus whatever firmware park offset
    its homing method used (0 for most axes, -20mm for Zg).

    Args:
      axis: The axis to compute the origin for.

    Returns:
      The engineering-unit position of firmware zero.
    """
    park_offset = self.get_park_position(axis)
    firmware_park = _FIRMWARE_PARK_MM.get(axis, 0.0)
    return park_offset - firmware_park

  def _validate_target(self, m: AxisMoveInfo) -> None:
    """Reject a move whose target falls outside the axis's configured range.

    Args:
      m: The move to validate.

    Raises:
      BravoError: If the resolved target is outside the axis's range.
    """
    ax_cfg = self._axis_config[m.axis]
    lo = ax_cfg.range.min_pos
    hi = ax_cfg.range.max_pos
    if m.absolute:
      target = m.position
    else:
      current = self.get_position(m.axis)
      target = current + m.position
    if not (lo <= target <= hi):
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=(
          f"Move target {target:.4f} mm on {axis_display_name(m.axis)} is outside "
          f"software limits [{lo:.4f}, {hi:.4f}]."
        ),
      )

  def _agile_7612_wait_for_settled(self, axes: list[Axis], timeout: float = 30.0) -> None:
    """Poll status until the given axes are settled.

    Only the status bytes for the axes actually moving are checked, not
    every byte in the response -- an uninitialized axis (e.g. W before
    homing) can show a permanently busy status byte, which would block
    settle detection if every byte were checked regardless of whether that
    axis was part of this move.

    Args:
      axes: The axes to wait for.
      timeout: Maximum time to wait, in seconds.

    Raises:
      BravoError: If any axis is still unsettled when ``timeout`` elapses.
    """
    ctrl1_positions = []
    ctrl2_positions = []
    for axis in axes:
      local = _local_axis_index(axis)
      if axis in _CONTROLLER_1_AXES:
        ctrl1_positions.append(local)
      else:
        ctrl2_positions.append(local)

    deadline = time.monotonic() + timeout
    self._require_connected()
    poll_count = 0
    while time.monotonic() < deadline:
      try:
        all_settled = True
        stuck_info = []
        if ctrl1_positions:
          resp1 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 0)
          if len(resp1) < 6:
            all_settled = False
            stuck_info.append("ctrl1: short response")
          else:
            for pos in ctrl1_positions:
              b = resp1[2 + pos]
              if b != 0x00 and (b & 0xF0) != self._STATUS_SETTLED:
                all_settled = False
                stuck_info.append(f"ctrl1[{pos}]=0x{b:02X}")
        if ctrl2_positions:
          resp2 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 4)
          if len(resp2) < 6:
            all_settled = False
            stuck_info.append("ctrl2: short response")
          else:
            for pos in ctrl2_positions:
              b = resp2[2 + pos]
              if b != 0x00 and (b & 0xF0) != self._STATUS_SETTLED:
                all_settled = False
                stuck_info.append(f"ctrl2[{pos}]=0x{b:02X}")
        if all_settled:
          return
        poll_count += 1
        if poll_count % 50 == 0:
          elapsed = timeout - (deadline - time.monotonic())
          logger.warning(
            "Settle wait %.1fs axes=%s stuck: %s",
            elapsed,
            [axis_display_name(a) for a in axes],
            ", ".join(stuck_info),
          )
      except (BravoError, TimeoutError, ConnectionError):
        pass
      time.sleep(self._MOVE_POLL_INTERVAL)
    stuck_info_final = []
    try:
      if ctrl1_positions:
        resp1 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 0)
        stuck_info_final.append(f"ctrl1={resp1.hex() if resp1 else 'None'}")
      if ctrl2_positions:
        resp2 = self._agile_7612_status_read(self._STATUS_REG_GENERAL, 4)
        stuck_info_final.append(f"ctrl2={resp2.hex() if resp2 else 'None'}")
    except Exception:  # noqa: BLE001 - this is best-effort diagnostics before raising
      pass
    logger.warning(
      "Settle TIMEOUT axes=%s final_status: %s",
      [axis_display_name(a) for a in axes],
      ", ".join(stuck_info_final),
    )
    raise BravoError(
      ErrorType.MOVE_TIMEOUT,
      custom_text=f"Timed out: {[axis_label(a) for a in axes]} ({timeout}s)",
    )

  # =================================================================
  # Homing -- two-phase with between-phase servo swaps
  # =================================================================

  def _homing_vel_accel(self, axis: Axis) -> tuple[float, float]:
    """Return an axis's homing velocity and acceleration, converted to ticks."""
    vel_mms, accel_mms2 = self._speed_for_level(axis, "homing")
    return (
      self._vel_to_ticks_per_ms(axis, vel_mms),
      self._accel_to_ticks_per_ms2(axis, accel_mms2),
    )

  def _homing_depart_direction(self, axis: Axis) -> int:
    """Return the direction sign that moves an axis away from its home sensor.

    Args:
      axis: The axis to look up.

    Returns:
      ``-1`` if the sensor sits at the positive end of travel, ``1``
      otherwise.
    """
    if self._axis_config[axis].home_in_positive_direction:
      return -1
    return 1

  def _home_sensor_bitmask(self, axis: Axis) -> int:
    """Return the bitmask for an axis's sensor flag in the register-0x10 status byte.

    Args:
      axis: The axis to look up.

    Returns:
      The configured bitmask, or a per-axis default if the axis's
      configuration leaves it unset.
    """
    bitmask = self._axis_config[axis].home_flag_bitmask
    if bitmask:
      return bitmask
    return _DEFAULT_HOME_SENSOR_BITMASK.get(axis, 4)

  def _agile_7612_servo_config_for_homing(self, axis: Axis) -> None:
    """Write every homing servo register for an axis, ignoring individual failures."""
    for reg, data in _homing_servo_registers(axis):
      try:
        self._agile_7612_servo_write(reg, data, axis)
      except BravoError as exc:
        logger.warning("Homing servo 0x%02X failed: %s", reg, exc)

  # =================================================================
  # Per-axis homing methods
  # =================================================================
  # Each axis has its own byte-exact sequence rather than one parameterized
  # direction-search routine, because the phase order and servo register
  # values are fixed per axis by the firmware, not derivable from a shared
  # formula.

  def _home_x(self) -> None:
    """Home the X axis.

    Reads register 0x10 (header 0x09) after servo configuration to pick
    the phase pattern: ``0x7F`` means the axis is on or past the sensor
    (2-phase: negative fast, positive slow); anything else means it is off
    the sensor (3-phase: positive fast, negative fast, positive slow).
    """
    comm = self._require_connected()
    axis: Axis = "x"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96)

    try:
      self._agile_7612_agile_read(0x60, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing X: reg 0x10 sensor byte=0x%02X -> %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing X: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing X: 2-phase (negative fast, positive slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)
    else:
      logger.info("Agile7612 homing X: 3-phase (positive fast, negative fast, positive slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis X homed")

  def _home_y(self) -> None:
    """Home the Y axis. Identical pattern to X: register 0x10 byte 2 picks the phase."""
    comm = self._require_connected()
    axis: Axis = "y"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 314.96)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing Y: reg 0x10 sensor byte=0x%02X -> %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing Y: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing Y: 2-phase (negative fast, positive slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)
    else:
      logger.info("Agile7612 homing Y: 3-phase (positive fast, negative fast, positive slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis Y homed")

  def _home_z(self) -> None:
    """Home the Z axis.

    Z's home sensor is at the top (negative end), so its directions are
    flipped relative to X/Y: on-sensor departs positive (down) and
    approaches negative (up) slowly; off-sensor approaches negative first.
    Parks with an absolute move to position 0 (the top) once homed.
    """
    comm = self._require_connected()
    axis: Axis = "z"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 1600.0)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing Z: reg 0x10 sensor byte=0x%02X -> %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing Z: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing Z: 2-phase (positive fast, negative slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)
    else:
      logger.info("Agile7612 homing Z: 3-phase (negative fast, positive fast, negative slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    info = self._move_info_cls(
      axis=axis,
      position=0.0,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout=30.0)

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis Z homed")

  def _home_w(self) -> None:
    """Home the W axis (plunger).

    On controller 1, home_in_positive_direction is False (sensor at the
    negative end, like Z). Register 0x10 selects the phase pattern the
    same way as X/Y. Parks with an absolute move to position 0 once homed.
    """
    comm = self._require_connected()
    axis: Axis = "w"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 448.0)

    try:
      self._agile_7612_agile_read(0x4A, axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    on_sensor = False
    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        sensor_byte = resp[2]
        on_sensor = bool(sensor_byte & self._home_sensor_bitmask(axis))
        logger.info(
          "Agile7612 homing W: reg 0x10 sensor byte=0x%02X -> %s",
          sensor_byte,
          "on sensor" if on_sensor else "off sensor",
        )
    except BravoError:
      logger.warning("Agile7612 homing W: reg 0x10 read failed, defaulting to 3-phase")

    if on_sensor:
      logger.info("Agile7612 homing W: 2-phase (positive fast, negative slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)
    else:
      logger.info("Agile7612 homing W: 3-phase (negative fast, positive fast, negative slow)")
      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=-large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

      self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
      self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
      info = self._move_info_cls(
        axis=axis,
        position=large_ticks,
        velocity=vel,
        acceleration=accel,
        absolute_move=False,
        check_for_homed=False,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    info = self._move_info_cls(
      axis=axis,
      position=0.0,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout=30.0)

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis W homed")

  def _home_g(self) -> None:
    """Home the G axis (gripper jaws).

    Controller 2, sensor at the negative end. Always uses the same
    2-phase pattern (positive fast, negative slow) regardless of starting
    position. Moves G to 0 both before and after the homing sequence,
    with a controller-2 fault reset around each move.
    """
    comm = self._require_connected()
    axis: Axis = "g"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 944.882)

    try:
      self._agile_7612_agile_read(0x5E, axis)
    except BravoError:
      pass
    try:
      info = self._move_info_cls(
        axis=axis,
        position=0.0,
        velocity=vel,
        acceleration=accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=30.0)
    except BravoError as exc:
      logger.warning("G homing: pre-move to 0 failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        logger.info("Agile7612 homing G: reg 0x10 byte=0x%02X (ignored -- always 2-phase)", resp[2])
    except BravoError:
      pass

    logger.info("Agile7612 homing G: 2-phase (positive fast, negative slow)")

    self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    try:
      self._agile_7612_agile_read(0x5E, axis)
    except BravoError:
      pass
    try:
      info = self._move_info_cls(
        axis=axis,
        position=0.0,
        velocity=vel,
        acceleration=accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go([axis])
      self._agile_7612_wait_for_settled([axis], timeout=30.0)
    except BravoError as exc:
      logger.warning("G homing: post-move to 0 failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis G homed")

  def _home_zg(self) -> None:
    """Home the Zg axis (gripper vertical travel).

    Controller 2, sensor at the top. Moves G to 0 first (a pre-move, with
    its own controller-2 fault reset), then always uses the same 2-phase
    pattern (positive fast/depart down, negative slow/approach up). Parks
    at -20mm (the nesting/docking position) once homed.
    """
    comm = self._require_connected()
    axis: Axis = "zg"
    vel, accel = self._homing_vel_accel(axis)
    home_reg = self._home_reg_for_axis(axis)
    large_ticks = _HOMING_DISTANCE_MM * self._ticks_per_unit.get(axis, 787.402)

    try:
      self._agile_7612_agile_read(0x5E, "g")
    except BravoError:
      pass
    try:
      g_home_reg = self._home_reg_for_axis("g")
      g_vel, g_accel = self._homing_vel_accel("g")
      info = self._move_info_cls(
        axis="g",
        position=0.0,
        velocity=g_vel,
        acceleration=g_accel,
        absolute_move=True,
        check_for_homed=True,
        home_complete_register=g_home_reg,
      )
      comm.send_command(CommandID.PREPARE_MOVE, info.pack())
      self._agile_7612_move_go(["g"])
      self._agile_7612_wait_for_settled(["g"], timeout=30.0)
    except BravoError as exc:
      logger.warning("Zg homing: G pre-move failed: %s", exc)
    self._agile_7612_fault_reset_ctrl2()

    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_ENABLE)
    except BravoError:
      pass

    self._agile_7612_servo_config_for_homing(axis)

    try:
      resp = self._agile_7612_ext_read(0x10, axis)
      if len(resp) >= 3:
        logger.info(
          "Agile7612 homing Zg: reg 0x10 byte=0x%02X (ignored -- always 2-phase)", resp[2]
        )
    except BravoError:
      pass

    logger.info("Agile7612 homing Zg: 2-phase (positive fast, negative slow)")

    self._agile_7612_servo_write(0xA3, _SERVO_A3_INITIAL, axis)
    self._agile_7612_servo_write(0xA4, _SERVO_A4_INITIAL, axis)
    info = self._move_info_cls(
      axis=axis,
      position=large_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    self._agile_7612_servo_write(0xA4, _SERVO_A4_SWAPPED, axis)
    self._agile_7612_servo_write(0xA3, _SERVO_A3_SWAPPED, axis)
    info = self._move_info_cls(
      axis=axis,
      position=-large_ticks,
      velocity=vel / 10.0,
      acceleration=accel,
      absolute_move=False,
      check_for_homed=False,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])
    self._agile_7612_wait_for_settled([axis], timeout=60.0)

    try:
      self._agile_7612_servo_write(0xA4, _SERVO_A4_RESET, axis)
    except BravoError:
      pass
    try:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x52
      raw[9] = crc8_maxim(raw, 9)
      self._send_agile(bytes(raw), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_write_home_reg(axis, _HOME_REG_HOMED)
    except BravoError:
      pass

    park_ticks = -20.0 * self._ticks_per_unit.get(axis, 787.402)
    info = self._move_info_cls(
      axis=axis,
      position=park_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    ctrl_base = 4 if axis in _CONTROLLER_2_AXES else 0
    raw = bytearray(10)
    raw[0] = 0x00
    raw[1] = _axis_bit(axis)
    raw[7] = 0x38
    raw[9] = crc8_maxim(raw, 9)
    comm.send_command(CommandID.DIRECT_AGILE_COMMAND, bytes(raw) + struct.pack("<B", ctrl_base))
    self._agile_7612_wait_for_settled([axis], timeout=30.0)

    self._homed[axis] = True
    self._capture_home_position(axis)
    logger.info("Axis Zg homed")

  def _agile_7612_move_axis_to_zero(self, axis: Axis) -> None:
    """Move an axis to absolute position 0, requiring it to already be homed.

    Args:
      axis: The axis to move.
    """
    vel_mms, accel_mms2 = self._speed_for_level(axis, "slow")
    self.move(
      [
        AxisMoveInfo(
          axis=axis, position=0.0, velocity=vel_mms, acceleration=accel_mms2, absolute=True
        )
      ]
    )

  def home_axes(self, axes: list[Axis], *, force: bool = False) -> None:
    """Home axes in a fixed, byte-exact sequence.

    Homes in the order Z (safety clearance first), Zg, G, X, then Y, then
    W -- each axis fully homed in turn, not overlapped. Faults are cleared
    on every axis first.

    Args:
      axes: The axes to home.
      force: Unused. Homing always runs unconditionally for the requested
        axes.
    """
    self.reset_faults(list(ALL_AXES))
    ctrl1_main = [a for a in axes if a in ("x", "y", "z", "w")]
    ctrl2_axes = [a for a in axes if a in ("g", "zg")]

    if "z" in ctrl1_main:
      self._home_z()

    if "zg" in ctrl2_axes:
      self._home_zg()

    if "g" in ctrl2_axes:
      self._home_g()

    xy_axes = [a for a in ctrl1_main if a in ("x", "y")]
    if "x" in xy_axes:
      self._home_x()
    if "y" in xy_axes:
      self._home_y()

    if "w" in ctrl1_main:
      self._home_w()

  # =================================================================
  # Force-controlled jog (tip pickup only, not UI jog)
  # =================================================================

  def jog(self, params: JogParams) -> float:
    """Execute a force-controlled jog via ``CMD_PREPARE_JOG`` plus a 0x80 trigger.

    Used only for tip pickup, where Z descends with a force limit until
    tips engage. Interactive jog moves go through :meth:`move` instead.

    Args:
      params: The jog parameters.

    Returns:
      The axis's final position, in mm.

    Raises:
      BravoError: If the axis has not been homed.
    """
    axis = params.axis
    if not self._homed[axis]:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=f"{axis_display_name(axis)} axis not initialized; home before jogging.",
      )
    comm = self._require_connected()

    current_pos = self.get_position(axis)
    if current_pos >= params.max_position:
      logger.warning(
        "jog: %s already at %.3f, past max_position %.3f -- skipping",
        axis_display_name(axis),
        current_pos,
        params.max_position,
      )
      return current_pos

    home_reg = self._home_reg_for_axis(axis)
    try:
      self._agile_7612_servo_write(0x23, bytes(7), axis)
    except BravoError:
      pass
    payload = struct.pack("<Bf", axis_code(axis), params.peak_current)
    payload += struct.pack(">H", home_reg)
    payload += struct.pack("<B", 0x01)
    comm.send_command(CommandID.PREPARE_JOG, payload)
    self._agile_7612_jog_trigger(axis)
    self._agile_7612_wait_for_settled([axis], timeout=30.0)
    self._tracked_position.pop(axis, None)
    final_pos = self.get_position(axis)

    if params.tolerance > 0 and final_pos > params.max_position + params.tolerance:
      logger.warning(
        "jog: %s final position %.3f exceeds max_position %.3f + tolerance %.3f",
        axis_display_name(axis),
        final_pos,
        params.max_position,
        params.tolerance,
      )

    try:
      comm.send_command(CommandID.QUERY_JOG_STATUS, timeout=1.0)
    except (BravoError, TimeoutError):
      pass
    return final_pos

  def tip_force_jog(self, axis: Axis, peak_current: float, max_position: float) -> float:
    """Force-controlled jog for tip-pickup experimentation.

    Kept separate from :meth:`jog` so this does not affect the working
    jog. Returns ``max_position`` rather than a freshly-read position,
    because a Controller 1 axis's raw position register wraps at 16 bits
    and cannot be trusted after a long force jog.

    Args:
      axis: The axis to jog.
      peak_current: Current limit for the force-controlled phase, in amps.
      max_position: The position limit the jog will not move past, in mm.

    Returns:
      ``max_position``.

    Raises:
      BravoError: If the axis has not been homed.
    """
    if not self._homed[axis]:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text=f"{axis_display_name(axis)} axis not initialized; home before jogging.",
      )
    comm = self._require_connected()

    current_pos = self.get_position(axis)
    if current_pos >= max_position:
      logger.warning(
        "tip_force_jog: %s at %.3f, past max %.3f -- skipping",
        axis_display_name(axis),
        current_pos,
        max_position,
      )
      return current_pos

    home_reg = self._home_reg_for_axis(axis)
    approach_mm = 8.0

    approach_target = max_position - approach_mm
    if approach_target > current_pos:
      logger.info("tip_force_jog: approaching Z=%.1f before force jog", approach_target)
      safe_vel, safe_accel = self._speed_for_level(axis, "safe")
      self.move(
        [
          AxisMoveInfo(
            axis=axis,
            position=approach_target,
            velocity=safe_vel,
            acceleration=safe_accel,
          )
        ],
        wait=True,
      )

    vel = self._vel_to_ticks_per_ms(axis, 10.0)
    accel = self._accel_to_ticks_per_ms2(axis, 100.0)
    target_ticks = self._to_ticks(axis, max_position)

    logger.warning(
      "tip_force_jog: %s force jog %.1f -> %.1f mm (%.0f ticks), peak=%.3fA",
      axis_display_name(axis),
      approach_target,
      max_position,
      target_ticks,
      peak_current,
    )

    info = self._move_info_cls(
      axis=axis,
      position=target_ticks,
      velocity=vel,
      acceleration=accel,
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go([axis])

    time.sleep(0.4)

    try:
      self._agile_7612_servo_write(0x02, bytes.fromhex("4ccccccc001000"), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_servo_write(0x23, bytes(7), axis)
    except BravoError:
      pass
    try:
      self._agile_7612_servo_write(0x23, bytes.fromhex("00000000001000"), axis)
    except BravoError:
      pass

    payload = struct.pack("<Bf", axis_code(axis), peak_current)
    payload += struct.pack(">H", home_reg)
    payload += struct.pack("<B", 0x01)
    comm.send_command(CommandID.PREPARE_JOG, payload)
    self._agile_7612_jog_trigger(axis)

    time.sleep(0.3)
    prev_raw: Optional[float] = None
    stable_count = 0
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
      try:
        raw = self._read_raw_position(axis)
        if prev_raw is not None and abs(raw - prev_raw) < 0.5:
          stable_count += 1
          if stable_count >= 3:
            break
        else:
          stable_count = 0
        prev_raw = raw
      except (BravoError, TimeoutError, ConnectionError):
        pass
      time.sleep(0.1)

    self._tracked_position[axis] = max_position

    try:
      comm.send_command(CommandID.QUERY_JOG_STATUS, timeout=1.0)
    except (BravoError, TimeoutError):
      pass
    return max_position

  # =================================================================
  # Gripper -- retry logic
  # =================================================================

  _GRIP_RETRIES = 4
  _GRIP_RETRY_DELAYS = [0.2, 0.3, 0.4, 0.6]
  _DETECT_GRIPPER_RETRIES = 4
  _DETECT_GRIPPER_DELAYS = [0.2, 0.3, 0.6, 1.0]

  def detect_gripper(self) -> GripperDetectionState:
    comm = self._require_connected()
    for attempt in range(self._DETECT_GRIPPER_RETRIES):
      try:
        data = comm.send_command(CommandID.DETECT_GRIPPER, timeout=2.0)
        if len(data) >= 1 and data[0] != 0:
          return GripperDetectionState(data[0])
      except (BravoError, TimeoutError):
        pass
      if attempt < self._DETECT_GRIPPER_RETRIES - 1:
        time.sleep(self._DETECT_GRIPPER_DELAYS[attempt])
    return GripperDetectionState.NOT_DETECTED

  def grip(self, speed: SpeedLevel, position: float, grip_lid: bool = False) -> None:
    comm = self._require_connected()
    vel, accel = self._speed_for_level("g", speed)
    home_reg = self._home_reg_for_axis("g")
    info = self._move_info_cls(
      axis="g",
      position=self._to_ticks("g", position),
      velocity=self._vel_to_ticks_per_ms("g", vel),
      acceleration=self._accel_to_ticks_per_ms2("g", accel),
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go(["g"])
    grip_stalled = False
    try:
      self._agile_7612_wait_for_settled(["g"], timeout=3.0)
    except BravoError:
      grip_stalled = True
    self._tracked_position["g"] = position
    self._agile_7612_fault_reset_ctrl2()
    if not grip_stalled:
      raise BravoError(
        ErrorType.COULD_NOT_MOVE_TO_POSITION,
        custom_text="Gripper closed to target without resistance -- no plate detected",
      )

  # =================================================================
  # Motor control
  # =================================================================

  def get_park_position(self, axis: Axis) -> float:
    """Return the axis's configured park position.

    Unlike the base class, which reports every axis parked at 0.0 (legacy
    Agile hardware exposes no per-axis park metadata), this returns the
    axis's configured ``homing_offset`` -- nonzero for Zg, which parks at
    -20mm rather than at its home sensor.

    Args:
      axis: The axis to look up.

    Returns:
      The configured park position, in mm.
    """
    return self._axis_config[axis].homing_offset

  def open_gripper(self, position: Optional[float] = None) -> None:
    comm = self._require_connected()
    target = 0.0 if position is None else float(position)
    vel, accel = self._speed_for_level("g", "safe")
    home_reg = self._home_reg_for_axis("g")
    info = self._move_info_cls(
      axis="g",
      position=self._to_ticks("g", target),
      velocity=self._vel_to_ticks_per_ms("g", vel),
      acceleration=self._accel_to_ticks_per_ms2("g", accel),
      absolute_move=True,
      check_for_homed=True,
      home_complete_register=home_reg,
    )
    comm.send_command(CommandID.PREPARE_MOVE, info.pack())
    self._agile_7612_move_go(["g"])
    self._agile_7612_wait_for_settled(["g"], timeout=30.0)
    self._tracked_position["g"] = target
    self._agile_7612_fault_reset_ctrl2()

  def enable_motor(self, axis: Axis) -> None:
    logger.debug("Agile7612: enable_motor(%s) no-op", axis_label(axis))

  def disable_motor(self, axis: Axis) -> None:
    logger.debug("Agile7612: disable_motor(%s) no-op", axis_label(axis))

  def is_motor_enabled(self, axis: Axis) -> bool:
    return self._homed[axis]

  def _is_estop_engaged(self) -> bool:
    try:
      state = self.query_state()
      return bool(state & DeviceStateFlag.ROBOT_DISABLE)
    except Exception:  # noqa: BLE001 - any query failure means "cannot confirm safe", not crash
      return False

  def recover(self, axes: Optional[list[Axis]] = None) -> dict[Axis, str]:
    """Clear faults and mark axes unhomed, so the next move re-homes them.

    Args:
      axes: The axes to recover. Defaults to every axis.

    Returns:
      A dict from axis to ``"enabled"``, for every recovered axis.

    Raises:
      BravoError: If the E-stop is still engaged.
    """
    if axes is None:
      axes = list(ALL_AXES)
    if self._is_estop_engaged():
      raise BravoError(
        ErrorType.ROBOT_DISABLE,
        custom_text="Cannot recover: E-stop still engaged. Release E-stop and retry.",
      )
    self.reset_faults(axes)
    for a in axes:
      self._homed[a] = False
    self._home_raw.clear()
    self._tracked_position.clear()
    return {a: "enabled" for a in axes}

  def read_plate_sensor(self, transient: float = 0.0) -> bool:
    logger.debug("Agile7612: read_plate_sensor() not supported, returning False")
    return False

  def scan_stack_with_gripper(
    self,
    *,
    start_zg: float,
    end_zg: float,
    speed: SpeedLevel,
    transient: float = 0.0,
  ) -> dict[str, Union[float, bool, None]]:
    raise BravoError(
      ErrorType.DARWIN_GENERIC,
      custom_text="scan_stack_with_gripper is not supported on Agile 7612 hardware",
    )

  def set_head_type(self, head_type: HeadType) -> None:
    """Record the installed head type, for callers that report it elsewhere.

    Args:
      head_type: The head type to report from now on.
    """
    self._head_type = head_type
    logger.info("Agile7612: head type set to %s", head_type)

  def get_head_type(self) -> HeadType:
    """Return the head type most recently set with :meth:`set_head_type`."""
    return self._head_type

  def read_head_identification(self) -> dict[str, object]:
    """Return head-identification data.

    Returns:
      A dict with ``eeprom_byte``, ``adc_counts``, and ``has_smart_head``.
      Agile 7612 hardware exposes none of these, so the values are always
      ``None``, ``0``, and ``False``.
    """
    return {"eeprom_byte": None, "adc_counts": 0, "has_smart_head": False}

  def reset_faults(self, axes: list[Axis]) -> None:
    for axis in axes:
      raw = bytearray(10)
      raw[0] = 0x00
      raw[1] = _axis_bit(axis)
      raw[7] = 0x31
      raw[9] = crc8_maxim(raw, 9)
      try:
        self._send_agile(bytes(raw), axis)
      except BravoError:
        pass

  def detect_smart_head(self) -> bool:
    return False

  def read_smart_head_type(self) -> int:
    return 0

  def clear_go_button(self) -> None:
    try:
      super().clear_go_button()
    except (BravoError, TimeoutError):
      logger.debug("Agile7612: clear_go_button not acknowledged")

  def read_head_adc(self) -> int:
    return 0
