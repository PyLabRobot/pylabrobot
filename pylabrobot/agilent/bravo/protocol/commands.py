"""V11/Agile command IDs and binary command payload structures.

Every struct layout here is 1-byte aligned (packed, no padding), matching the
wire format the Rabbit microcontroller expects: nothing is inserted for
alignment, so a payload's byte length is exactly the sum of its field widths.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from ..types import (
  _AXIS_BY_CODE,
  Axis,
  LightColor,
  LightCommand,
  axis_code,
  light_command_period_ms,
)
from .agile_packet import AGILE_PACKET_SIZE

__all__ = [
  "AGILE_PACKET_SIZE",
  "CommandID",
  "AgileMoveInfo",
  "AgileJogInfo",
  "LightCommandData",
  "GripperParams",
  "SmartHeadEEPROMData",
  "EEPROMAddress",
  "DEFAULT_COMMAND_TIMEOUT",
  "MAX_COMMAND_RETRIES",
]

# ---------------------------------------------------------------------------
# Command IDs (PC -> Rabbit)
# ---------------------------------------------------------------------------


class CommandID(IntEnum):
  """Command IDs sent to the Bravo via the V11DeviceComm protocol.

  0x01-0x02, 0x04-0x07, and 0x0E-0x0F are reserved by the Rabbit firmware
  (deprecated firmware response, meta-framework, abort/pause/unpause/ignore,
  and protocol-version query respectively) and are not assigned here.
  """

  QUERY_VERSION = 0x00
  PING_DEVICE = 0xA0
  DIRECT_AGILE_COMMAND = 0xA1
  PREPARE_MOVE = 0xA2
  QUERY_ROBOT_DISABLE = 0xA3
  QUERY_MOTOR_POWER = 0xA4
  CLEAR_MOTOR_POWER_FAULT = 0xA5
  GET_POSITION = 0xA6
  QUERY_STATE = 0xA7
  CLEAR_GO_BUTTON = 0xA8
  GO_BUTTON_PRESSED = 0xA9
  PREPARE_JOG = 0xAA
  STOP = 0xAB
  QUERY_JOG_STATUS = 0xAE
  SET_LIGHT = 0xB0
  CLEAR_LIGHTS = 0xB1
  DETECT_GRIPPER = 0xB2
  READ_AD_WEIGH_PAD = 0xB3
  GRIP = 0xB4
  DETECT_SMART_HEAD = 0xB5
  GET_EEPROM_DATA = 0xB6
  WRITE_EEPROM_DATA = 0xB7
  WRITE_SERIAL_NUMBER = 0xB8
  GET_SERIAL_NUMBER = 0xB9


# ---------------------------------------------------------------------------
# Binary command payload structures (1-byte aligned, packed, no padding)
# ---------------------------------------------------------------------------


@dataclass
class AgileMoveInfo:
  """Move command payload for ``CMD_PREPARE_MOVE`` on the legacy Agile generation.

  All position/velocity/acceleration values are in encoder ticks and ticks/ms.

  Attributes:
    axis: The axis this move targets.
    position: Target position (absolute) or delta (relative), in ticks.
    velocity: Move velocity, in ticks/ms.
    acceleration: Move acceleration, in ticks/ms^2.
    absolute_move: Whether ``position`` is absolute rather than relative.
    check_for_homed: Whether the controller should refuse the move if the
      axis has not been homed.
    home_complete_register: The Agile register whose value confirms this
      axis's home flag, packed as a uint32. The Agile 7612 generation packs
      this same field as a uint16; see
      :class:`~.agile_7612_commands.Agile7612MoveInfo`.
  """

  axis: Axis
  position: float
  velocity: float
  acceleration: float
  absolute_move: bool = True
  check_for_homed: bool = True
  home_complete_register: int = 0

  # u8 + 3*float + 2*u8 + u32 = 1 + 12 + 2 + 4 = 19 bytes
  _PACK_FORMAT = "<Bfff BB I"

  def pack(self) -> bytes:
    """Pack this move command into its 19-byte wire encoding.

    Returns:
      The packed payload.
    """
    return struct.pack(
      self._PACK_FORMAT,
      axis_code(self.axis),
      self.position,
      self.velocity,
      self.acceleration,
      1 if self.absolute_move else 0,
      1 if self.check_for_homed else 0,
      self.home_complete_register,
    )

  @classmethod
  def unpack(cls, data: bytes) -> AgileMoveInfo:
    """Unpack a move command from its 19-byte wire encoding.

    Args:
      data: At least 19 bytes, payload first.

    Returns:
      The decoded move command.
    """
    axis_val, pos, vel, accel, abs_move, check_homed, home_reg = struct.unpack(
      cls._PACK_FORMAT, data[: struct.calcsize(cls._PACK_FORMAT)]
    )
    return cls(
      axis=_AXIS_BY_CODE[axis_val],
      position=pos,
      velocity=vel,
      acceleration=accel,
      absolute_move=bool(abs_move),
      check_for_homed=bool(check_homed),
      home_complete_register=home_reg,
    )


@dataclass
class AgileJogInfo:
  """Jog command payload for ``CMD_PREPARE_JOG``.

  Attributes:
    axis: The axis to jog.
    velocity: Jog velocity, in ticks/ms.
    acceleration: Jog acceleration, in ticks/ms^2.
    max_position: Position limit the jog must not cross, in ticks.
    tolerance: Position tolerance, in ticks.
    peak_current: Peak motor current, as a fraction of the axis's maximum.
  """

  axis: Axis
  velocity: float
  acceleration: float
  max_position: float
  tolerance: float
  peak_current: float

  _PACK_FORMAT = "<Bfffff"

  def pack(self) -> bytes:
    """Pack this jog command into its wire encoding.

    Returns:
      The packed payload.
    """
    return struct.pack(
      self._PACK_FORMAT,
      axis_code(self.axis),
      self.velocity,
      self.acceleration,
      self.max_position,
      self.tolerance,
      self.peak_current,
    )

  @classmethod
  def unpack(cls, data: bytes) -> AgileJogInfo:
    """Unpack a jog command from its wire encoding.

    Args:
      data: At least ``struct.calcsize(AgileJogInfo._PACK_FORMAT)`` bytes,
        payload first.

    Returns:
      The decoded jog command.
    """
    vals = struct.unpack(cls._PACK_FORMAT, data[: struct.calcsize(cls._PACK_FORMAT)])
    return cls(
      axis=_AXIS_BY_CODE[vals[0]],
      velocity=vals[1],
      acceleration=vals[2],
      max_position=vals[3],
      tolerance=vals[4],
      peak_current=vals[5],
    )


@dataclass
class LightCommandData:
  """Wire payload for ``CMD_SET_LIGHT``: a color, blink period, and duty cycle.

  This mirrors the wire format exactly, including its millisecond period
  field: unlike :class:`~pylabrobot.agilent.bravo.types.LightCommand`, which
  stores the period in seconds for the rest of the driver to use,
  ``period_ms`` here is the literal 32-bit millisecond count the firmware
  reads. Build one from a :class:`~pylabrobot.agilent.bravo.types.LightCommand`
  with :meth:`from_light_command` rather than passing
  ``int(command.period)`` as ``period_ms`` -- truncating a sub-second period
  to an int silently produces 0, which the firmware reads as solid instead of
  blinking.

  Attributes:
    light: The color channel(s) to light.
    period_ms: Blink period, in whole milliseconds. 0 means solid.
    duty_cycle: Fraction of each period the light is on, 0.0 to 1.0.
  """

  light: LightColor
  period_ms: int = 0
  duty_cycle: float = 1.0

  _PACK_FORMAT = "<BIf"  # u8 + u32 + float = 9 bytes

  def pack(self) -> bytes:
    """Pack this light command into its 9-byte wire encoding.

    Returns:
      The packed payload.
    """
    return struct.pack(self._PACK_FORMAT, int(self.light), self.period_ms, self.duty_cycle)

  @classmethod
  def unpack(cls, data: bytes) -> LightCommandData:
    """Unpack a light command from its 9-byte wire encoding.

    Args:
      data: At least 9 bytes, payload first.

    Returns:
      The decoded light command.
    """
    light, period, duty = struct.unpack(cls._PACK_FORMAT, data[: struct.calcsize(cls._PACK_FORMAT)])
    return cls(light=LightColor(light), period_ms=period, duty_cycle=duty)

  @classmethod
  def from_light_command(cls, command: LightCommand) -> LightCommandData:
    """Build a wire payload from a driver-level light command.

    Converts ``command.period`` from seconds to the wire's millisecond count
    via :func:`~pylabrobot.agilent.bravo.types.light_command_period_ms`,
    rather than truncating it directly.

    Args:
      command: The light command to convert.

    Returns:
      The equivalent wire payload.
    """
    return cls(
      light=command.color,
      period_ms=light_command_period_ms(command),
      duty_cycle=command.duty_cycle,
    )

  def to_light_command(self) -> LightCommand:
    """Convert this wire payload back to a driver-level light command.

    Returns:
      The equivalent :class:`~pylabrobot.agilent.bravo.types.LightCommand`,
      with :attr:`period_ms` converted back to seconds.
    """
    return LightCommand(
      color=LightColor(self.light),
      period=self.period_ms / 1000.0,
      duty_cycle=self.duty_cycle,
    )


@dataclass
class GripperParams:
  """Gripper command payload for ``CMD_GRIP``.

  Attributes:
    grip_current: Current limit during the grip move, in amps.
    grip_velocity: Grip move velocity.
    grip_acceleration: Grip move acceleration.
    target_position: Target jaw position.
    position_tolerance: Position tolerance for detecting a successful grip.
    max_gripper_current: Absolute current ceiling for the gripper axis.
    original_max_pos_error: The G-axis's normal max-position-error limit, to
      restore after the grip move's own tolerance is done with it.
    original_velocity: The G-axis's normal velocity, to restore afterward.
    original_acceleration: The G-axis's normal acceleration, to restore
      afterward.
    ticks_per_eng_unit: Encoder ticks per engineering unit for the G-axis.
  """

  grip_current: float
  grip_velocity: float
  grip_acceleration: float
  target_position: float
  position_tolerance: float
  max_gripper_current: float
  original_max_pos_error: float
  original_velocity: float
  original_acceleration: float
  ticks_per_eng_unit: float

  _PACK_FORMAT = "<ffffffffff"  # 10 floats = 40 bytes

  def pack(self) -> bytes:
    """Pack this gripper command into its 40-byte wire encoding.

    Returns:
      The packed payload.
    """
    return struct.pack(
      self._PACK_FORMAT,
      self.grip_current,
      self.grip_velocity,
      self.grip_acceleration,
      self.target_position,
      self.position_tolerance,
      self.max_gripper_current,
      self.original_max_pos_error,
      self.original_velocity,
      self.original_acceleration,
      self.ticks_per_eng_unit,
    )

  @classmethod
  def unpack(cls, data: bytes) -> GripperParams:
    """Unpack a gripper command from its 40-byte wire encoding.

    Args:
      data: At least 40 bytes, payload first.

    Returns:
      The decoded gripper command.
    """
    vals = struct.unpack(cls._PACK_FORMAT, data[: struct.calcsize(cls._PACK_FORMAT)])
    return cls(*vals)


@dataclass
class SmartHeadEEPROMData:
  """EEPROM read/write payload for ``CMD_GET_EEPROM_DATA``/``CMD_WRITE_EEPROM_DATA``.

  Attributes:
    address: The EEPROM address to read or write.
    length: Number of valid bytes in ``data``, 1-5.
    data: Up to 5 bytes of EEPROM content.
  """

  address: int
  length: int
  data: bytes = b""

  _PACK_FORMAT = "<BB5s"  # u8 + u8 + 5 bytes = 7

  def pack(self) -> bytes:
    """Pack this EEPROM command into its 7-byte wire encoding.

    Returns:
      The packed payload, with ``data`` padded or truncated to 5 bytes.
    """
    padded = (self.data + b"\x00" * 5)[:5]
    return struct.pack(self._PACK_FORMAT, self.address, self.length, padded)

  @classmethod
  def unpack(cls, raw: bytes) -> SmartHeadEEPROMData:
    """Unpack an EEPROM command from its 7-byte wire encoding.

    Args:
      raw: At least 7 bytes, payload first.

    Returns:
      The decoded EEPROM command, with ``data`` trimmed to ``length`` bytes.
    """
    addr, length, data_bytes = struct.unpack(
      cls._PACK_FORMAT, raw[: struct.calcsize(cls._PACK_FORMAT)]
    )
    return cls(address=addr, length=length, data=data_bytes[:length])


# ---------------------------------------------------------------------------
# Smart Head EEPROM address map
# ---------------------------------------------------------------------------


class EEPROMAddress(IntEnum):
  """EEPROM field addresses on a smart pipetting head."""

  FIRMWARE_VERSION = 0x00
  HEAD_TYPE = 0x01
  HOMING_OFFSET = 0x02  # 2 bytes
  W_AXIS_TRAVEL = 0x04  # 5 bytes (cumulative mm)
  W_AXIS_DIR_CHANGES = 0x09  # 4 bytes
  PM_DATE = 0x0D  # 2 bytes
  W_TRAVEL_PRIOR_PM = 0x0F  # 5 bytes
  W_DIR_CHANGES_PRIOR_PM = 0x14  # 4 bytes
  TOTAL_EEPROM_WRITES = 0x18  # 3 bytes
  SERIAL_NUMBER_LENGTH = 0x1B  # 1 byte
  SERIAL_NUMBER = 0x1C  # 1-20 bytes


# ---------------------------------------------------------------------------
# Default timeouts
# ---------------------------------------------------------------------------

DEFAULT_COMMAND_TIMEOUT = 2.0
MAX_COMMAND_RETRIES = 5
