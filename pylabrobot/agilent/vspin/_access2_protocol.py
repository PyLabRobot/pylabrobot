"""Agilent Access2 command and FTDI framing primitives.

The Access2 command layer uses a one-byte command identifier followed by a
little-endian 16-bit payload length. The PLR-supported FTDI connection wraps
that command in a Velocity11 envelope and protects it with CRC-16/XMODEM::

  0x11 | 0x05 | command length (big endian) | 0x00 | command | CRC (big endian)
"""

from __future__ import annotations

import dataclasses

from pylabrobot.io.binary import Reader, Writer

VELOCITY11_HEADER = 0x11
VELOCITY11_PACKET_TYPE = 0x05
VELOCITY11_CHANNEL = 0x00
MAX_INNER_FRAME_LENGTH = 4096

# Access2 command identifiers.
GET_FIRMWARE_VERSION = 0x00
INITIALIZE = 0x10
CLOSE = 0x12
PING = 0x14
GET_HARDWARE_VERSION = 0x16
GET_STATUS = 0x20
WRITE_FLASH = 0x22
READ_FLASH = 0x24
USE_FLASH = 0x26
FORMAT_FLASH = 0x28
RESET_ACCESS2_CIRCUIT_BREAKER = 0x30
RESET_VSPIN_CIRCUIT_BREAKER = 0x32
RESET_ESTOP = 0x34
SERVO_SWITCH = 0x36
HOME = 0x40
JOG_AXIS = 0x42
MOVE_TO_LOCATION = 0x44
MOVE_TO_POSITION = 0x46
GET_SENSOR_VALUES = 0x50

# Axis addresses.
AXIS_GRIPPER = 1
AXIS_Y = 2
AXIS_Z = 3

# Stored teachpoint indices.
TEACHPOINT_PARK = 0
TEACHPOINT_PICK = 1
TEACHPOINT_BUCKET_1 = 2
TEACHPOINT_BUCKET_2 = 3
TEACHPOINT_HOVER = 4

# Motion profile indices.
PROFILE_STATIC = 0
PROFILE_HOMING = 1
PROFILE_DYNAMIC_EMPTY = 2
PROFILE_DYNAMIC_FULL = 3
PROFILE_GRIP_NORMALLY = 4
PROFILE_GRIP_GENTLY = 5

# Speed indices.
SPEED_SLOW = 0
SPEED_MEDIUM = 1
SPEED_FAST = 2

# Access2 status bits.
STATUS_INITIALIZED = 0x01
STATUS_HOMED = 0x02
STATUS_ESTOP_SET = 0x04
STATUS_ESTOP_ACTIVE = 0x08
STATUS_MOTOR_POWER_FAULT = 0x10
STATUS_OPTICAL_PLATE_SENSOR = 0x20

# Access2 exposes a status byte for each axis. Bit 0 is used as the motion-complete
# indicator. The remaining bits are intentionally left opaque: Access2-specific
# meanings have not been verified and cannot safely be inferred from raw PIC-SERVO.
AXIS_STATUS_MOVE_DONE = 0x01

# Captured sensor word returned when no plate is present at the queried handoff.
SENSOR_NO_PLATE = 0x00000003


class Access2ProtocolError(ValueError):
  """Raised when an Access2 command, response, or FTDI envelope is invalid."""


@dataclasses.dataclass(frozen=True)
class Access2Reply:
  """A validated Access2 response with its command result removed."""

  response_id: int
  data: bytes


@dataclasses.dataclass(frozen=True)
class Access2Status:
  """Decoded controller status and optional per-axis positions."""

  access2_status: int
  vspin_status: int
  gripper_status: int | None = None
  gripper_position: float | None = None
  y_status: int | None = None
  y_position: float | None = None
  z_status: int | None = None
  z_position: float | None = None

  @property
  def initialized(self) -> bool:
    return bool(self.access2_status & STATUS_INITIALIZED)

  @property
  def homed(self) -> bool:
    return bool(self.access2_status & STATUS_HOMED)

  @property
  def estop_set(self) -> bool:
    return bool(self.access2_status & STATUS_ESTOP_SET)

  @property
  def estop_active(self) -> bool:
    return bool(self.access2_status & STATUS_ESTOP_ACTIVE)

  @property
  def motor_power_fault(self) -> bool:
    return bool(self.access2_status & STATUS_MOTOR_POWER_FAULT)

  @property
  def optical_plate_sensor(self) -> bool:
    return bool(self.access2_status & STATUS_OPTICAL_PLATE_SENSOR)

  def axis_status(self, axis: int) -> int | None:
    """Return the PIC-SERVO status byte for ``axis`` when full status is available."""
    if axis == AXIS_GRIPPER:
      return self.gripper_status
    if axis == AXIS_Y:
      return self.y_status
    if axis == AXIS_Z:
      return self.z_status
    raise ValueError(f"Unknown Access2 axis: {axis}")

  def axis_position(self, axis: int) -> float | None:
    """Return the position for ``axis`` when full status is available."""
    if axis == AXIS_GRIPPER:
      return self.gripper_position
    if axis == AXIS_Y:
      return self.y_position
    if axis == AXIS_Z:
      return self.z_position
    raise ValueError(f"Unknown Access2 axis: {axis}")


def crc16_xmodem(data: bytes) -> int:
  """Return CRC-16/CCITT-XMODEM for ``data``."""
  crc = 0
  for value in data:
    crc ^= value << 8
    for _ in range(8):
      if crc & 0x8000:
        crc = ((crc << 1) ^ 0x1021) & 0xFFFF
      else:
        crc = (crc << 1) & 0xFFFF
  return crc


def build_command(command_id: int, data: bytes = b"") -> bytes:
  """Build the transport-independent Access2 command frame."""
  _validate_u8(command_id, "command_id")
  if len(data) > 0xFFFF:
    raise ValueError(f"Access2 payload is too long: {len(data)} bytes")
  return Writer().u8(command_id).u16(len(data)).raw_bytes(data).finish()


def build_ftdi_frame(command: bytes) -> bytes:
  """Wrap one Access2 command in the current PLR FTDI envelope."""
  if not 3 <= len(command) <= MAX_INNER_FRAME_LENGTH:
    raise ValueError(
      f"Access2 command must contain from 3 through {MAX_INNER_FRAME_LENGTH} bytes, "
      f"got {len(command)}"
    )
  body = (
    Writer(little_endian=False)
    .u8(VELOCITY11_HEADER)
    .u8(VELOCITY11_PACKET_TYPE)
    .u16(len(command))
    .u8(VELOCITY11_CHANNEL)
    .raw_bytes(command)
    .finish()
  )
  return body + Writer(little_endian=False).u16(crc16_xmodem(body)).finish()


def parse_ftdi_header(header: bytes) -> int:
  """Validate a five-byte FTDI header and return its inner-frame length."""
  if len(header) != 5:
    raise Access2ProtocolError(f"Access2 FTDI header has {len(header)} bytes, expected 5")
  reader = Reader(header, little_endian=False)
  header_byte = reader.u8()
  packet_type = reader.u8()
  inner_length = reader.u16()
  channel = reader.u8()
  if header_byte != VELOCITY11_HEADER or packet_type != VELOCITY11_PACKET_TYPE:
    raise Access2ProtocolError(
      f"Unexpected Access2 FTDI header 0x{header_byte:02x} 0x{packet_type:02x}"
    )
  if channel != VELOCITY11_CHANNEL:
    raise Access2ProtocolError(f"Unexpected Access2 FTDI channel 0x{channel:02x}")
  if inner_length > MAX_INNER_FRAME_LENGTH:
    raise Access2ProtocolError(
      f"Access2 FTDI inner frame exceeds {MAX_INNER_FRAME_LENGTH} bytes: {inner_length}"
    )
  return inner_length


def parse_ftdi_frame(frame: bytes) -> bytes:
  """Validate an Access2 FTDI envelope and return its inner frame."""
  if len(frame) < 10:
    raise Access2ProtocolError(f"Access2 FTDI frame is too short: {frame.hex()}")
  inner_length = parse_ftdi_header(frame[:5])
  expected_length = inner_length + 7
  if len(frame) != expected_length:
    raise Access2ProtocolError(
      f"Access2 FTDI frame has {len(frame)} bytes, expected {expected_length}"
    )
  reader = Reader(frame[5:], little_endian=False)
  inner = reader.raw_bytes(inner_length)
  received_crc = reader.u16()
  expected_crc = crc16_xmodem(frame[:-2])
  if received_crc != expected_crc:
    raise Access2ProtocolError(
      f"Access2 FTDI CRC mismatch: received 0x{received_crc:04x}, expected 0x{expected_crc:04x}"
    )
  return inner


def parse_reply(frame: bytes, request_id: int) -> Access2Reply:
  """Parse an inner Access2 response and validate its response ID and result."""
  _validate_u8(request_id, "request_id")
  if len(frame) < 4:
    raise Access2ProtocolError(f"Access2 response is too short: {frame.hex()}")
  reader = Reader(frame)
  response_id = reader.u8()
  data_length = reader.u16()
  data = reader.remaining()
  if len(data) != data_length:
    raise Access2ProtocolError(
      f"Access2 response has {len(data)} data bytes, expected {data_length}"
    )
  if not data:
    raise Access2ProtocolError("Access2 response does not contain a command result byte")
  expected_response_id = (request_id + 1) & 0xFF
  if response_id != expected_response_id:
    raise Access2ProtocolError(
      f"Access2 response ID is 0x{response_id:02x}, expected 0x{expected_response_id:02x}"
    )
  result = Reader(data).u8()
  if result != 0:
    raise Access2ProtocolError(
      f"Access2 command 0x{request_id:02x} failed with result 0x{result:02x}"
    )
  return Access2Reply(response_id=response_id, data=data[1:])


def parse_ftdi_reply(frame: bytes, request_id: int) -> Access2Reply:
  """Parse an FTDI-wrapped reply to ``request_id``."""
  return parse_reply(parse_ftdi_frame(frame), request_id)


def decode_status(data: bytes) -> Access2Status:
  """Decode either the short or full Access2 status payload."""
  if len(data) < 4:
    raise Access2ProtocolError(f"Access2 status has only {len(data)} bytes")
  reader = Reader(data)
  access2_status = reader.u8()
  vspin_status = reader.u8()
  if len(data) == 4:
    return Access2Status(access2_status=access2_status, vspin_status=vspin_status)
  if len(data) < 17:
    raise Access2ProtocolError(
      f"Access2 status has {len(data)} bytes, expected either 4 or at least 17"
    )
  return Access2Status(
    access2_status=access2_status,
    vspin_status=vspin_status,
    gripper_status=reader.u8(),
    gripper_position=reader.f32(),
    y_status=reader.u8(),
    y_position=reader.f32(),
    z_status=reader.u8(),
    z_position=reader.f32(),
  )


def decode_sensor_values(data: bytes) -> int:
  """Decode the Access2 sensor bit word."""
  if len(data) != 4:
    raise Access2ProtocolError(f"Access2 sensor response has {len(data)} bytes, expected 4")
  return Reader(data).u32()


def decode_firmware_version(data: bytes) -> str:
  """Decode the controller's null-padded ASCII firmware version."""
  return data.rstrip(b"\x00").decode("ascii", errors="replace")


def decode_hardware_version(data: bytes) -> int:
  """Decode the controller's signed 16-bit hardware version."""
  if len(data) < 2:
    raise Access2ProtocolError(
      f"Access2 hardware version has {len(data)} bytes, expected at least 2"
    )
  return Reader(data).i16()


def build_ping(data: bytes = b"") -> bytes:
  return build_command(PING, data)


def build_get_firmware_version() -> bytes:
  return build_command(GET_FIRMWARE_VERSION)


def build_get_hardware_version() -> bytes:
  return build_command(GET_HARDWARE_VERSION)


def build_initialize() -> bytes:
  return build_command(INITIALIZE)


def build_close() -> bytes:
  return build_command(CLOSE)


def build_get_status() -> bytes:
  return build_command(GET_STATUS)


def build_home() -> bytes:
  return build_command(HOME)


def build_get_sensor_values() -> bytes:
  return build_command(GET_SENSOR_VALUES)


def build_read_flash(address: int, length: int) -> bytes:
  _validate_u16(address, "address")
  _validate_u16(length, "length")
  return build_command(READ_FLASH, Writer().u16(address).u16(length).finish())


def build_move_to_teachpoint(
  teachpoint: int,
  z_offset: float,
  plate_height: float,
  profile: int = PROFILE_DYNAMIC_EMPTY,
  speed: int = SPEED_SLOW,
) -> bytes:
  for value, name in ((teachpoint, "teachpoint"), (profile, "profile"), (speed, "speed")):
    _validate_u8(value, name)
  data = Writer().u8(teachpoint).f32(z_offset).f32(plate_height).u8(profile).u8(speed).finish()
  return build_command(MOVE_TO_LOCATION, data)


def build_move_axis_to_position(
  axis: int,
  position: float,
  profile: int = PROFILE_DYNAMIC_EMPTY,
  speed: int = SPEED_SLOW,
) -> bytes:
  for value, name in ((axis, "axis"), (profile, "profile"), (speed, "speed")):
    _validate_u8(value, name)
  data = Writer().u8(axis).f32(position).u8(profile).u8(speed).finish()
  return build_command(MOVE_TO_POSITION, data)


def build_jog_axis(
  axis: int,
  displacement: float,
  profile: int = PROFILE_DYNAMIC_EMPTY,
  speed: int = SPEED_SLOW,
) -> bytes:
  for value, name in ((axis, "axis"), (profile, "profile"), (speed, "speed")):
    _validate_u8(value, name)
  data = Writer().u8(axis).f32(displacement).u8(profile).u8(speed).finish()
  return build_command(JOG_AXIS, data)


def _validate_u8(value: int, name: str) -> None:
  if not 0 <= value <= 0xFF:
    raise ValueError(f"{name} must fit in an unsigned 8-bit integer")


def _validate_u16(value: int, name: str) -> None:
  if not 0 <= value <= 0xFFFF:
    raise ValueError(f"{name} must fit in an unsigned 16-bit integer")
