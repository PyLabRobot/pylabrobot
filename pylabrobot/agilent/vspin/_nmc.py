"""JR Kerr NMC protocol primitives used by the Agilent VSpin.

The VSpin contains a PIC-SERVO module for the rotor and a PIC-IO module for
the door, bucket lock, and safety signals. Commands share this frame shape::

  0xAA | module address | (payload length << 4) | command | payload | checksum

The response shape is determined by the status mask configured for each
module. Responses do not have a delimiter.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Optional

from pylabrobot.io.binary import Reader, Writer

SYNC_BYTE = 0xAA

PIC_SERVO_ADDRESS = 0x01
PIC_IO_ADDRESS = 0x02
GROUP_ADDRESS = 0xFF

PIC_SERVO_MODULE_TYPE = 0
PIC_IO_MODULE_TYPE = 2

# NMC command codes. The command occupies the low nibble of the command byte.
CMD_RESET_POSITION = 0x0
CMD_SET_IO_DIRECTION = 0x0
CMD_SET_ADDRESS = 0x1
CMD_DEFINE_STATUS = 0x2
CMD_READ_STATUS = 0x3
CMD_LOAD_TRAJECTORY = 0x4
CMD_START_MOTION = 0x5
CMD_SET_OUTPUT = 0x6
CMD_SET_GAIN = 0x6
CMD_STOP_MOTOR = 0x7
CMD_IO_CONTROL = 0x8
CMD_SET_HOMING = 0x9
CMD_SET_BAUD = 0xA
CMD_CLEAR_BITS = 0xB
CMD_NO_OP = 0xE
CMD_HARD_RESET = 0xF

# PIC-SERVO LOAD_TRAJECTORY mode bits.
LOAD_POSITION = 0x01
LOAD_VELOCITY = 0x02
LOAD_ACCELERATION = 0x04
LOAD_PWM = 0x08
ENABLE_SERVO = 0x10
VELOCITY_MODE = 0x20
START_NOW = 0x80

# PIC-SERVO STOP_MOTOR mode bits.
AMPLIFIER_ENABLE = 0x01
MOTOR_OFF = 0x02
STOP_ABRUPT = 0x04
STOP_SMOOTH = 0x08
STOP_HERE = 0x10

# PIC-SERVO status-mask fields.
SEND_POSITION = 0x01
SEND_ANALOG = 0x02
SEND_VELOCITY = 0x04
SEND_AUXILIARY = 0x08
SEND_HOME = 0x10
SEND_MODULE_ID = 0x20
SEND_POSITION_ERROR = 0x40
SEND_PATH_POINTS = 0x80

# PIC-IO status-mask fields. Some values overlap the PIC-SERVO fields.
SEND_INPUTS = 0x01
SEND_ANALOG_1 = 0x02
SEND_ANALOG_2 = 0x04
SEND_ANALOG_3 = 0x08
SEND_TIMER = 0x10
SEND_SYNC_INPUTS = 0x40
SEND_SYNC_TIMER = 0x80

# Response status-byte bits.
STATUS_MOVE_DONE = 0x01
STATUS_CHECKSUM_ERROR = 0x02
STATUS_OVERCURRENT = 0x04
STATUS_POWER_ON = 0x08
STATUS_POSITION_ERROR = 0x10
STATUS_LIMIT_1 = 0x20
STATUS_LIMIT_2 = 0x40
STATUS_HOMING_IN_PROGRESS = 0x80

# VSpin PIC-IO input-bit indices and output-bit indices.
INPUT_AMPLIFIER_FAULT = 0
INPUT_SPINNING = 1
INPUT_IMBALANCE = 2
INPUT_BUCKET_UNLOCKED = 3
INPUT_BUCKET_LOCKED = 4
INPUT_DOOR_OPEN = 6
INPUT_DOOR_LOCKED = 7
INPUT_AMPLIFIER_ENABLED = 11

OUTPUT_VERSION_TOGGLE = 5
OUTPUT_BUCKET_LOCK_CYLINDER = 8
OUTPUT_DOOR_CYLINDER = 9
OUTPUT_DOOR_LOCK_CYLINDER = 10

# VSpin trajectory constants.
COUNTS_PER_REVOLUTION = 8000
DEFAULT_ROTOR_RADIUS_CM = 10.0
DEFAULT_MAX_VELOCITY_RPM = 3000.0
NMC_VELOCITY_PER_RPM = 4473.925
NMC_ACCELERATION_AT_FULL_SCALE = 916.19328
NOMINAL_MAX_ACCELERATION_RPM_PER_SECOND = 400.0
DEFAULT_SPIN_TARGET_HEADROOM = 5.0

BAUD_RATE_CODES = {
  19200: 63,
  57600: 20,
  115200: 10,
}


class NMCProtocolError(ValueError):
  """Raised when an NMC frame or response is malformed."""


@dataclasses.dataclass(frozen=True)
class NMCResponse:
  """A checksum-verified NMC response."""

  status: int
  data: bytes


@dataclasses.dataclass(frozen=True)
class ServoStatus:
  """Decoded PIC-SERVO response fields selected by a status mask."""

  status: int
  position: Optional[int] = None
  analog: Optional[int] = None
  velocity: Optional[int] = None
  auxiliary: Optional[int] = None
  home_position: Optional[int] = None
  module_type: Optional[int] = None
  module_version: Optional[int] = None
  position_error: Optional[int] = None
  path_points: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class IOStatus:
  """Decoded PIC-IO response fields selected by a status mask."""

  status: int
  inputs: Optional[int] = None
  analog_1: Optional[int] = None
  analog_2: Optional[int] = None
  analog_3: Optional[int] = None
  timer: Optional[int] = None
  module_type: Optional[int] = None
  module_version: Optional[int] = None
  sync_inputs: Optional[int] = None
  sync_timer: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class ServoGains:
  """PIC-SERVO gain values in controller-native units."""

  proportional: int
  derivative: int
  integral: int
  integration_limit: int
  output_limit: int
  current_limit: int
  position_error_limit: int
  servo_rate: int
  deadband: int


def build_command(address: int, command: int, data: bytes = b"") -> bytes:
  """Build one NMC command frame.

  Args:
    address: Module address from 0 through 32, or the group address ``0xFF``.
    command: Four-bit NMC command code.
    data: Command payload of at most 15 bytes.

  Returns:
    A complete command including sync byte and checksum.
  """
  if not (0 <= address <= 32 or address == GROUP_ADDRESS):
    raise ValueError(f"NMC address must be from 0 through 32 or 0xFF, got {address}")
  if not 0 <= command <= 0x0F:
    raise ValueError(f"NMC command must fit in four bits, got {command}")
  if len(data) > 0x0F:
    raise ValueError(f"NMC payload must contain at most 15 bytes, got {len(data)}")

  command_byte = (len(data) << 4) | command
  body = bytes([address, command_byte]) + data
  return bytes([SYNC_BYTE]) + body + bytes([sum(body) & 0xFF])


def parse_response(frame: bytes, expected_data_length: int) -> NMCResponse:
  """Parse and checksum one fixed-length NMC response."""
  if expected_data_length < 0:
    raise ValueError("expected_data_length must not be negative")
  expected_frame_length = expected_data_length + 2
  if len(frame) != expected_frame_length:
    raise NMCProtocolError(
      f"NMC response has {len(frame)} bytes, expected {expected_frame_length}: {frame.hex()}"
    )

  status = frame[0]
  data = frame[1:-1]
  checksum = frame[-1]
  expected_checksum = (status + sum(data)) & 0xFF
  if checksum != expected_checksum:
    raise NMCProtocolError(
      "NMC response checksum mismatch: "
      f"received 0x{checksum:02x}, expected 0x{expected_checksum:02x}"
    )
  if status & STATUS_CHECKSUM_ERROR:
    raise NMCProtocolError(f"NMC module rejected the command: status 0x{status:02x}")
  return NMCResponse(status=status, data=data)


def servo_status_data_length(mask: int) -> int:
  """Return the PIC-SERVO response data length for ``mask``."""
  _validate_status_mask(mask)
  lengths = (
    (SEND_POSITION, 4),
    (SEND_ANALOG, 1),
    (SEND_VELOCITY, 2),
    (SEND_AUXILIARY, 1),
    (SEND_HOME, 4),
    (SEND_MODULE_ID, 2),
    (SEND_POSITION_ERROR, 2),
    (SEND_PATH_POINTS, 1),
  )
  return sum(length for bit, length in lengths if mask & bit)


def io_status_data_length(mask: int) -> int:
  """Return the PIC-IO response data length for ``mask``."""
  _validate_status_mask(mask)
  lengths = (
    (SEND_INPUTS, 2),
    (SEND_ANALOG_1, 1),
    (SEND_ANALOG_2, 1),
    (SEND_ANALOG_3, 1),
    (SEND_TIMER, 4),
    (SEND_MODULE_ID, 2),
    (SEND_SYNC_INPUTS, 2),
    (SEND_SYNC_TIMER, 4),
  )
  return sum(length for bit, length in lengths if mask & bit)


def parse_servo_status(frame: bytes, mask: int) -> ServoStatus:
  """Parse a PIC-SERVO response using its active status mask."""
  response = parse_response(frame, servo_status_data_length(mask))
  return decode_servo_status(response, mask)


def decode_servo_status(response: NMCResponse, mask: int) -> ServoStatus:
  """Decode a checksum-verified PIC-SERVO response using its status mask."""
  expected_length = servo_status_data_length(mask)
  if len(response.data) != expected_length:
    raise NMCProtocolError(
      f"PIC-SERVO status has {len(response.data)} data bytes, expected {expected_length}"
    )
  reader = Reader(response.data)

  position = None
  analog = None
  velocity = None
  auxiliary = None
  home_position = None
  module_type = None
  module_version = None
  position_error = None
  path_points = None

  if mask & SEND_POSITION:
    position = reader.i32()
  if mask & SEND_ANALOG:
    analog = reader.u8()
  if mask & SEND_VELOCITY:
    velocity = reader.i16()
  if mask & SEND_AUXILIARY:
    auxiliary = reader.u8()
  if mask & SEND_HOME:
    home_position = reader.i32()
  if mask & SEND_MODULE_ID:
    module_type = reader.u8()
    module_version = reader.u8()
  if mask & SEND_POSITION_ERROR:
    position_error = reader.i16()
  if mask & SEND_PATH_POINTS:
    path_points = reader.u8()

  return ServoStatus(
    status=response.status,
    position=position,
    analog=analog,
    velocity=velocity,
    auxiliary=auxiliary,
    home_position=home_position,
    module_type=module_type,
    module_version=module_version,
    position_error=position_error,
    path_points=path_points,
  )


def parse_io_status(frame: bytes, mask: int) -> IOStatus:
  """Parse a PIC-IO response using its active status mask."""
  response = parse_response(frame, io_status_data_length(mask))
  return decode_io_status(response, mask)


def decode_io_status(response: NMCResponse, mask: int) -> IOStatus:
  """Decode a checksum-verified PIC-IO response using its status mask."""
  expected_length = io_status_data_length(mask)
  if len(response.data) != expected_length:
    raise NMCProtocolError(
      f"PIC-IO status has {len(response.data)} data bytes, expected {expected_length}"
    )
  reader = Reader(response.data)

  inputs = None
  analog_1 = None
  analog_2 = None
  analog_3 = None
  timer = None
  module_type = None
  module_version = None
  sync_inputs = None
  sync_timer = None

  if mask & SEND_INPUTS:
    inputs = reader.u16()
  if mask & SEND_ANALOG_1:
    analog_1 = reader.u8()
  if mask & SEND_ANALOG_2:
    analog_2 = reader.u8()
  if mask & SEND_ANALOG_3:
    analog_3 = reader.u8()
  if mask & SEND_TIMER:
    timer = reader.u32()
  if mask & SEND_MODULE_ID:
    module_type = reader.u8()
    module_version = reader.u8()
  if mask & SEND_SYNC_INPUTS:
    sync_inputs = reader.u16()
  if mask & SEND_SYNC_TIMER:
    sync_timer = reader.u32()

  return IOStatus(
    status=response.status,
    inputs=inputs,
    analog_1=analog_1,
    analog_2=analog_2,
    analog_3=analog_3,
    timer=timer,
    module_type=module_type,
    module_version=module_version,
    sync_inputs=sync_inputs,
    sync_timer=sync_timer,
  )


def build_set_address(address: int, group_address: int = GROUP_ADDRESS) -> bytes:
  """Build an address-assignment command for the next unaddressed module."""
  return build_command(0, CMD_SET_ADDRESS, bytes([address, group_address]))


def build_define_status(address: int, mask: int) -> bytes:
  """Build a command that sets the module's response status mask."""
  _validate_status_mask(mask)
  return build_command(address, CMD_DEFINE_STATUS, bytes([mask]))


def build_read_status(address: int, mask: int) -> bytes:
  """Build a one-time status read using ``mask``."""
  _validate_status_mask(mask)
  return build_command(address, CMD_READ_STATUS, bytes([mask]))


def build_no_op(address: int) -> bytes:
  """Build a no-op command, normally used to request current status."""
  return build_command(address, CMD_NO_OP)


def build_set_baud(baud_rate: int) -> bytes:
  """Build a group command that changes the NMC bus baud rate."""
  try:
    code = BAUD_RATE_CODES[baud_rate]
  except KeyError as exc:
    supported = ", ".join(str(rate) for rate in sorted(BAUD_RATE_CODES))
    raise ValueError(f"unsupported NMC baud rate {baud_rate}; expected one of {supported}") from exc
  return build_command(GROUP_ADDRESS, CMD_SET_BAUD, bytes([code]))


def build_hard_reset() -> bytes:
  """Build an NMC group hard-reset command."""
  return build_command(GROUP_ADDRESS, CMD_HARD_RESET)


def build_set_output(address: int, output_word: int) -> bytes:
  """Build a PIC-IO output-word command."""
  _validate_u16(output_word, "output_word")
  return build_command(address, CMD_SET_OUTPUT, Writer().u16(output_word).finish())


def build_set_io_direction(address: int, direction_word: int) -> bytes:
  """Build a PIC-IO direction-word command."""
  _validate_u16(direction_word, "direction_word")
  return build_command(address, CMD_SET_IO_DIRECTION, Writer().u16(direction_word).finish())


def build_stop_motor(address: int, mode: int) -> bytes:
  """Build a PIC-SERVO stop command."""
  _validate_u8(mode, "mode")
  return build_command(address, CMD_STOP_MOTOR, bytes([mode]))


def build_clear_bits(address: int) -> bytes:
  """Build a PIC-SERVO command that clears sticky status bits."""
  return build_command(address, CMD_CLEAR_BITS)


def build_reset_position(address: int) -> bytes:
  """Build a PIC-SERVO command that resets the current position to zero."""
  return build_command(address, CMD_RESET_POSITION)


def build_set_homing(address: int, mode: int) -> bytes:
  """Build a PIC-SERVO homing-mode command."""
  _validate_u8(mode, "mode")
  return build_command(address, CMD_SET_HOMING, bytes([mode]))


def build_set_gain(address: int, gains: ServoGains) -> bytes:
  """Build a PIC-SERVO gain command."""
  for value, name in (
    (gains.proportional, "proportional"),
    (gains.derivative, "derivative"),
    (gains.integral, "integral"),
    (gains.integration_limit, "integration_limit"),
    (gains.position_error_limit, "position_error_limit"),
  ):
    if not -0x8000 <= value <= 0x7FFF:
      raise ValueError(f"{name} must fit in a signed 16-bit integer")
  _validate_u8(gains.output_limit, "output_limit")
  _validate_u8(gains.current_limit, "current_limit")
  _validate_u8(gains.servo_rate, "servo_rate")
  _validate_u8(gains.deadband, "deadband")
  data = (
    Writer()
    .i16(gains.proportional)
    .i16(gains.derivative)
    .i16(gains.integral)
    .i16(gains.integration_limit)
    .u8(gains.output_limit)
    .u8(gains.current_limit)
    .i16(gains.position_error_limit)
    .u8(gains.servo_rate)
    .u8(gains.deadband)
    .finish()
  )
  return build_command(address, CMD_SET_GAIN, data)


def build_load_trajectory(
  address: int,
  mode: int,
  *,
  position: Optional[int] = None,
  velocity: Optional[int] = None,
  acceleration: Optional[int] = None,
  pwm: Optional[int] = None,
) -> bytes:
  """Build a PIC-SERVO trajectory command from the fields selected by ``mode``."""
  _validate_u8(mode, "mode")
  writer = Writer().u8(mode)

  if mode & LOAD_POSITION:
    if position is None:
      raise ValueError("position is required when LOAD_POSITION is set")
    _validate_i32(position, "position")
    writer.i32(position)
  elif position is not None:
    raise ValueError("position was provided but LOAD_POSITION is not set")

  if mode & LOAD_VELOCITY:
    if velocity is None:
      raise ValueError("velocity is required when LOAD_VELOCITY is set")
    _validate_u32(velocity, "velocity")
    writer.u32(velocity)
  elif velocity is not None:
    raise ValueError("velocity was provided but LOAD_VELOCITY is not set")

  if mode & LOAD_ACCELERATION:
    if acceleration is None:
      raise ValueError("acceleration is required when LOAD_ACCELERATION is set")
    _validate_u32(acceleration, "acceleration")
    writer.u32(acceleration)
  elif acceleration is not None:
    raise ValueError("acceleration was provided but LOAD_ACCELERATION is not set")

  if mode & LOAD_PWM:
    if pwm is None:
      raise ValueError("pwm is required when LOAD_PWM is set")
    _validate_u8(pwm, "pwm")
    writer.u8(pwm)
  elif pwm is not None:
    raise ValueError("pwm was provided but LOAD_PWM is not set")

  return build_command(address, CMD_LOAD_TRAJECTORY, writer.finish())


def rcf_to_rpm(rcf: float, rotor_radius: float = DEFAULT_ROTOR_RADIUS_CM) -> float:
  """Convert relative centrifugal force to RPM for a radius in centimeters."""
  if rcf < 0:
    raise ValueError("rcf must not be negative")
  if rotor_radius <= 0:
    raise ValueError("rotor_radius must be greater than zero")
  return math.sqrt(rcf / (1.118e-5 * rotor_radius))


def rpm_to_rcf(rpm: float, rotor_radius: float = DEFAULT_ROTOR_RADIUS_CM) -> float:
  """Convert RPM to relative centrifugal force for a radius in centimeters."""
  if rpm < 0:
    raise ValueError("rpm must not be negative")
  if rotor_radius <= 0:
    raise ValueError("rotor_radius must be greater than zero")
  return 1.118e-5 * rotor_radius * rpm**2


def rpm_to_nmc_velocity(rpm: float, servo_rate: int = 1) -> int:
  """Convert RPM to the unsigned NMC trajectory velocity field."""
  if rpm < 0:
    raise ValueError("rpm must not be negative")
  if servo_rate < 1:
    raise ValueError("servo_rate must be at least 1")
  return int(NMC_VELOCITY_PER_RPM * rpm * servo_rate)


def acceleration_to_nmc(acceleration: float, servo_rate: int = 1) -> int:
  """Convert a PLR acceleration fraction to the NMC trajectory field."""
  _validate_fraction(acceleration, "acceleration")
  if servo_rate < 1:
    raise ValueError("servo_rate must be at least 1")
  return int(NMC_ACCELERATION_AT_FULL_SCALE * servo_rate**2 * acceleration)


def acceleration_rpm_per_second(acceleration: float) -> float:
  """Return nominal physical acceleration for a PLR acceleration fraction."""
  _validate_fraction(acceleration, "acceleration")
  return NOMINAL_MAX_ACCELERATION_RPM_PER_SECOND * acceleration


def acceleration_counts_per_second_squared(acceleration: float) -> float:
  """Return nominal encoder acceleration for a PLR acceleration fraction."""
  return acceleration_rpm_per_second(acceleration) * COUNTS_PER_REVOLUTION / 60.0


def predicted_ramp_time(rpm: float, acceleration: float) -> float:
  """Return nominal seconds required to ramp from zero to ``rpm``."""
  if rpm < 0:
    raise ValueError("rpm must not be negative")
  return rpm / acceleration_rpm_per_second(acceleration)


def acceleration_distance(rpm: float, acceleration: float) -> int:
  """Return encoder counts traversed during a nominal zero-to-``rpm`` ramp."""
  if rpm < 0:
    raise ValueError("rpm must not be negative")
  velocity_counts_per_second = rpm * COUNTS_PER_REVOLUTION / 60.0
  distance = velocity_counts_per_second**2 / (
    2.0 * acceleration_counts_per_second_squared(acceleration)
  )
  return int(distance)


def spin_target_distance(
  rpm: float,
  duration: float,
  acceleration: float,
  headroom: float = DEFAULT_SPIN_TARGET_HEADROOM,
) -> int:
  """Return the reference trajectory's deliberately distant target delta.

  The VSpin is stopped by a later zero-velocity trajectory, not by reaching
  this position. The additional distance prevents the position target from
  ending the spin before PLR commands deceleration.
  """
  if rpm < 0:
    raise ValueError("rpm must not be negative")
  if duration < 0:
    raise ValueError("duration must not be negative")
  if headroom < 0:
    raise ValueError("headroom must not be negative")
  cruise_and_headroom = int(COUNTS_PER_REVOLUTION * rpm * (duration + headroom) / 60.0)
  return cruise_and_headroom + 2 * acceleration_distance(rpm, acceleration)


def nearest_encoder_position(
  current_position: int,
  target_remainder: int,
  counts_per_revolution: int = COUNTS_PER_REVOLUTION,
) -> int:
  """Return the nearest absolute encoder position matching ``target_remainder``."""
  if counts_per_revolution <= 0:
    raise ValueError("counts_per_revolution must be greater than zero")
  target_remainder %= counts_per_revolution
  current_remainder = current_position % counts_per_revolution
  delta = (target_remainder - current_remainder) % counts_per_revolution
  if delta > counts_per_revolution / 2:
    delta -= counts_per_revolution
  return current_position + delta


def _validate_status_mask(mask: int) -> None:
  _validate_u8(mask, "status mask")


def _validate_fraction(value: float, name: str) -> None:
  if not 0 < value <= 1:
    raise ValueError(f"{name} must be greater than 0 and at most 1")


def _validate_u8(value: int, name: str) -> None:
  if not 0 <= value <= 0xFF:
    raise ValueError(f"{name} must fit in an unsigned byte")


def _validate_u16(value: int, name: str) -> None:
  if not 0 <= value <= 0xFFFF:
    raise ValueError(f"{name} must fit in an unsigned 16-bit integer")


def _validate_u32(value: int, name: str) -> None:
  if not 0 <= value <= 0xFFFFFFFF:
    raise ValueError(f"{name} must fit in an unsigned 32-bit integer")


def _validate_i32(value: int, name: str) -> None:
  if not -(2**31) <= value <= 2**31 - 1:
    raise ValueError(f"{name} must fit in a signed 32-bit integer")
