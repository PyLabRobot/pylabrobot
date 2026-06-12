"""Motor, linear-axis, and filter-wheel components for the Celigo."""

from __future__ import annotations

import asyncio
import contextlib
import math
import struct
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional, Tuple

from pylabrobot.celigo.config import (
  AxisConfig,
  CeligoConfig,
  FilterWheelConfig,
  LinearAxisConfig,
)
from pylabrobot.celigo.errors import CeligoError
from pylabrobot.celigo.protocol import complete_cleanup, require_payload_length

if TYPE_CHECKING:
  from pylabrobot.celigo.celigo import Celigo

LinearAxisName = Literal["x", "y", "z"]
MotorControllerFirmwareVersion = Tuple[int, int]

# AllMotion status byte: 0x20 set == ready, low nibble == error code.
_EZ_READY_BIT = 0x20
_EZ_ERROR_MASK = 0x0F

# EZStepper ASCII command codes.
_EZ_MOVE_ABSOLUTE = "A"
_EZ_MOVE_POSITIVE = "P"
_EZ_MOVE_NEGATIVE = "D"
_EZ_HOME = "Z"
_EZ_SET_VELOCITY = "V"
_EZ_SET_ACCELERATION = "L"
_EZ_SET_MOVE_CURRENT = "m"
_EZ_SET_HOLD_CURRENT = "h"
_EZ_SET_POLARITY = "f"
_EZ_SET_POSITIVE_DIRECTION = "F"
_EZ_SET_SPECIAL_MODE = "N"
_EZ_SET_MODE = "n"
_EZ_SET_ENCODER_RATIO = "aE"
_EZ_SET_OVERLOAD_TIMEOUT = "au"
_EZ_SET_COARSE_WINDOW = "aC"
_EZ_SET_FINE_WINDOW = "ac"
_EZ_SET_INTEGRATION_PERIOD = "x"
_EZ_SET_RESPONSE_TIME = "aP"
_EZ_SET_BACKLASH = "K"
_EZ_SET_S_CURVE = "aj"
_EZ_TERMINATE = "T"
_EZ_QUERY_FIRMWARE = "&"
_EZ_QUERY = "?"
_EZ_QUERY_STATUS = "Q"
_EZ_QUERY_ENCODER_POSITION = 8
_EZ_QUERY_FLAGS = 4

_EZ_MODE_ENABLE_LIMITS = 0x02
_EZ_MODE_ENABLE_POSITION_CORRECTION = 0x08
_EZ_MODE_ENABLE_STEP_AND_DIRECTION = 0x20
_EZ_MODE_ENABLE_MOTOR_SLAVE_TO_ENCODER = 0x40

_EZ_SPECIAL_ENCODER_NO_INDEX = 1
_EZ_SPECIAL_ENCODER_WITH_INDEX = 2
_EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE = 6

_LIMIT_OPTO_1 = 0x04
_LIMIT_OPTO_2 = 0x08
_LIMIT_ALL = 0x1F
_ETX = "\x03"

# Controller-board motor-tunnel opcodes and statuses.
_CMD_MOTOR_QUERY = 44
_CMD_MOTOR_QUERY_WITH_LENGTH = 47
_LENGTH_PREFIXED_COMMAND_MINIMUM_FIRMWARE = (1, 3, 0)
_NO_CONTROLLER_ERROR = 0
_NO_MOTOR_NUMBER = 5011
_BAD_MOTOR_NUMBER = 5012
_MOTOR_COMMUNICATION_ERROR = 5025
_MOTOR_QUERY_ATTEMPTS = 5
_MOTOR_COMMAND_MAX_BYTES = 512
_STX_BYTE = b"\x02"
_ETX_BYTE = b"\x03"


@dataclass(frozen=True)
class _MotionProfile:
  """EZStepper motion values in controller-native units.

  Linear-axis rates are converted from millimeters using ``mm_per_encoder_tick``.
  Optical-axis configurations already use encoder rates. Current values are percentages
  of the motor's rated current.
  """

  velocity_ticks_per_second: int
  acceleration_ticks_per_second_squared: int
  move_current_percent: Optional[int]
  hold_current_percent: Optional[int]


@dataclass(frozen=True)
class _EZResponse:
  """Parsed AllMotion reply: ready flag, error code, and response text."""

  ready: bool
  error_code: int
  response_text: str

  @property
  def ok(self) -> bool:
    return self.error_code == 0


def _ez_motor_address(axis_index: int) -> str:
  """Return the AllMotion address character for a motor index."""
  return str(axis_index) if 0 < axis_index < 10 else chr(48 + axis_index)


def _make_ez_command(
  axis_index: int,
  command_tokens: str,
  execute: bool,
) -> str:
  """Build an EZStepper command string: ``/<addr><tokens>[R]\\r``."""
  return f"/{_ez_motor_address(axis_index)}{command_tokens}{'R' if execute else ''}\r"


def _parse_ez_response(raw_response: str) -> _EZResponse:
  """Parse an AllMotion reply string."""
  master_prefix_index = raw_response.find("/0")
  status_position: Optional[int]
  if master_prefix_index >= 0 and master_prefix_index + 2 < len(raw_response):
    status_position = master_prefix_index + 2
  else:
    status_position = next(
      (index for index, character in enumerate(raw_response) if ord(character) & 0x40),
      None,
    )
  if status_position is None:
    raise CeligoError(f"No EZStepper status byte in reply: {raw_response!r}")
  status = ord(raw_response[status_position])
  response_text = raw_response[status_position + 1 :]
  for terminator in (_ETX, "\r", "\n"):
    cut = response_text.find(terminator)
    if cut >= 0:
      response_text = response_text[:cut]
  return _EZResponse(
    ready=bool(status & _EZ_READY_BIT),
    error_code=status & _EZ_ERROR_MASK,
    response_text=response_text,
  )


def _encode_oem_command(command: str) -> bytes:
  start = command.rfind("/")
  end = command.find("\r", start + 1)
  if start < 0 or end <= start + 1:
    raise ValueError(f"Invalid EZStepper command framing: {command!r}")
  command_body = command[start + 1 : end]
  address, command_tokens = command_body[0], command_body[1:]
  frame = _STX_BYTE + f"{address}1{command_tokens}".encode("ascii") + _ETX_BYTE
  checksum = 0
  for value in frame:
    checksum ^= value
  return frame + bytes([checksum])


def _decode_oem_response(response_packet: bytes) -> str:
  start = response_packet.rfind(_STX_BYTE)
  if start < 0:
    raise CeligoError("Invalid OEM motor response: missing STX")
  end = response_packet.find(_ETX_BYTE, start + 1)
  if end < 0:
    raise CeligoError("Invalid OEM motor response: missing ETX")
  if end - start - 1 < 2:
    raise CeligoError("Invalid OEM motor response: payload is too short")
  if end + 1 >= len(response_packet):
    raise CeligoError("Invalid OEM motor response: missing checksum")

  calculated_checksum = 0
  for value in response_packet[start : end + 1]:
    calculated_checksum ^= value
  received_checksum = response_packet[end + 1]
  if received_checksum != calculated_checksum:
    raise CeligoError(
      "OEM motor response checksum failure: "
      f"received {received_checksum:#04x}, calculated {calculated_checksum:#04x}"
    )
  return "/" + response_packet[start + 1 : end].decode("latin-1")


class MotorController:
  """EZStepper command transport tunneled through the Celigo controller board."""

  def __init__(self, board: "Celigo") -> None:
    self._board = board

  @property
  def move_timeout(self) -> float:
    return self._board.move_timeout

  @property
  def _uses_length_prefixed_commands(self) -> bool:
    firmware_version = self._board.controller_firmware_version
    if firmware_version is None:
      raise CeligoError("Motor-command framing is unavailable before controller identification")
    return firmware_version >= _LENGTH_PREFIXED_COMMAND_MINIMUM_FIRMWARE

  async def send_command(self, command: str) -> str:
    """Send a complete EZStepper command string and return its device reply."""
    uses_length_prefixed_commands = self._uses_length_prefixed_commands
    encoded_command = (
      _encode_oem_command(command) if uses_length_prefixed_commands else command.encode("ascii")
    )
    if len(encoded_command) > _MOTOR_COMMAND_MAX_BYTES:
      raise ValueError(
        f"Motor command is {len(encoded_command)} bytes; maximum is {_MOTOR_COMMAND_MAX_BYTES}"
      )
    payload = encoded_command if uses_length_prefixed_commands else encoded_command + b"\x00"
    opcode = _CMD_MOTOR_QUERY_WITH_LENGTH if uses_length_prefixed_commands else _CMD_MOTOR_QUERY
    attempts = _MOTOR_QUERY_ATTEMPTS if uses_length_prefixed_commands else 1

    for attempt in range(attempts):
      response = await self._board.send_command(opcode, payload)
      require_payload_length(response, 2, "motor query")
      (extended_status,) = struct.unpack_from(">H", response, 0)
      if extended_status in (_NO_MOTOR_NUMBER, _BAD_MOTOR_NUMBER):
        raise CeligoError(
          f"Invalid motor number (status {extended_status}) for command {command!r}"
        )
      if extended_status == _MOTOR_COMMUNICATION_ERROR:
        if uses_length_prefixed_commands and attempt < attempts - 1:
          continue
        raise CeligoError(f"Motor communication error for command {command!r}")
      if extended_status != _NO_CONTROLLER_ERROR:
        raise CeligoError(f"Unexpected motor status {extended_status} for command {command!r}")

      require_payload_length(response, 4, "motor query")
      (response_length,) = struct.unpack_from(">H", response, 2)
      require_payload_length(response, 4 + response_length, "motor query")
      motor_response = response[4 : 4 + response_length]
      if not uses_length_prefixed_commands:
        return motor_response.decode("latin-1")
      try:
        return _decode_oem_response(motor_response)
      except CeligoError:
        if attempt == attempts - 1:
          raise

    raise CeligoError(f"Motor query failed after {attempts} attempts: {command!r}")


def _parse_motor_controller_firmware_version(
  response_text: str,
) -> MotorControllerFirmwareVersion:
  """Extract the numeric version from an EZStepper identification response."""
  for token in response_text.replace(",", " ").split():
    if token[:1].lower() != "v":
      continue
    major, separator, minor = token[1:].partition(".")
    if separator and major.isdigit() and minor.isdigit():
      return int(major), int(minor)
  raise CeligoError(f"Could not parse EZStepper firmware response {response_text!r}")


class StepperMotor:
  """One addressed EZStepper motor on the Celigo controller."""

  def __init__(self, controller: MotorController, axis_index: int) -> None:
    if axis_index <= 0:
      raise ValueError("axis_index must be positive")
    self._controller = controller
    self.axis_index = axis_index

  async def send_command(
    self,
    command_tokens: str,
    execute: bool = True,
  ) -> _EZResponse:
    """Send EZStepper command tokens to this motor."""
    command = _make_ez_command(
      self.axis_index,
      command_tokens,
      execute,
    )
    return _parse_ez_response(await self._controller.send_command(command))

  async def request_motor_controller_firmware_version(
    self,
  ) -> MotorControllerFirmwareVersion:
    """Read this motor's EZStepper controller firmware version."""
    response = await self.send_command(_EZ_QUERY_FIRMWARE, execute=False)
    if not response.ok:
      raise CeligoError(
        f"motor {self.axis_index} firmware query failed (code {response.error_code})"
      )
    return _parse_motor_controller_firmware_version(response.response_text)

  async def request_encoder_ratio(self) -> float:
    """Read the configured ratio of encoder ticks to motor ticks."""
    response = await self.send_command(
      f"{_EZ_QUERY}{_EZ_SET_ENCODER_RATIO}",
      execute=False,
    )
    if not response.ok:
      raise CeligoError(
        f"motor {self.axis_index} encoder-ratio query failed (code {response.error_code})"
      )
    return int(response.response_text) / 1000.0

  async def request_encoder_ticks(self) -> int:
    """Read the current encoder position in ticks."""
    response = await self.send_command(
      f"{_EZ_QUERY}{_EZ_QUERY_ENCODER_POSITION}",
      execute=False,
    )
    if not response.ok:
      raise CeligoError(
        f"motor {self.axis_index} encoder query failed (code {response.error_code})"
      )
    return int(response.response_text)

  async def wait_until_ready(self, timeout: Optional[float] = None) -> int:
    """Wait until the motor is ready and return its settled encoder position."""
    selected_timeout = self._controller.move_timeout if timeout is None else timeout
    deadline = time.monotonic() + selected_timeout
    while time.monotonic() < deadline:
      response = await self.send_command(_EZ_QUERY_STATUS, execute=False)
      if not response.ok:
        raise CeligoError(f"motor {self.axis_index} reported error {response.error_code}")
      if response.ready:
        return await self.request_encoder_ticks()
      await asyncio.sleep(0.05)
    raise TimeoutError(f"motor {self.axis_index} not ready within timeout")

  async def _set_mode(self, motor_mode: int) -> None:
    response = await self.send_command(f"{_EZ_SET_MODE}{motor_mode}")
    if not response.ok:
      raise CeligoError(f"motor {self.axis_index} mode change failed (code {response.error_code})")

  async def _set_parameter(
    self,
    parameter_token: str,
    parameter_value: int,
    operation_description: str,
  ) -> None:
    response = await self.send_command(f"{parameter_token}{parameter_value}")
    if not response.ok:
      raise CeligoError(
        f"motor {self.axis_index} {operation_description} failed (code {response.error_code})"
      )

  async def _terminate(self) -> None:
    response = await self.send_command(_EZ_TERMINATE, execute=False)
    if not response.ok:
      raise CeligoError(f"motor {self.axis_index} stop failed (code {response.error_code})")


class Axis:
  """One configured motorized Celigo mechanism."""

  def __init__(
    self,
    controller: MotorController,
    name: str,
    config: AxisConfig,
  ) -> None:
    if not config.enabled or config.axis_index <= 0:
      raise ValueError("Axis requires an enabled configuration with a positive axis_index")
    self._controller = controller
    self.name = name
    self.config = config
    self.motor = StepperMotor(controller, config.axis_index)
    self._supports_accurate_encoder_index = False
    self._initialized = False

  @property
  def axis_index(self) -> int:
    return self.motor.axis_index

  @property
  def is_initialized(self) -> bool:
    return self._initialized

  def _rate_to_encoder_tick_rate(self, configured_rate: float) -> int:
    if not math.isfinite(configured_rate) or configured_rate <= 0:
      raise CeligoError(
        f"{self.config.motion_name or f'motor {self.axis_index}'} has invalid "
        f"configured rate {configured_rate}"
      )
    encoder_tick_rate = round(configured_rate)
    if encoder_tick_rate <= 0:
      raise CeligoError(
        f"{self.config.motion_name or f'motor {self.axis_index}'} rate "
        f"{configured_rate} rounds to zero encoder ticks"
      )
    return encoder_tick_rate

  def _motor_mode(self, enable_position_correction: bool = True) -> int:
    motor_mode = 0
    if self.config.mode_enable_limits:
      motor_mode |= _EZ_MODE_ENABLE_LIMITS
    if enable_position_correction and self.config.mode_enable_position_correction:
      motor_mode |= _EZ_MODE_ENABLE_POSITION_CORRECTION
    if self.config.mode_enable_step_and_direction:
      motor_mode |= _EZ_MODE_ENABLE_STEP_AND_DIRECTION
    if self.config.mode_enable_motor_slave_to_encoder:
      motor_mode |= _EZ_MODE_ENABLE_MOTOR_SLAVE_TO_ENCODER
    return motor_mode

  def _motion_profile(self) -> _MotionProfile:
    return _MotionProfile(
      velocity_ticks_per_second=self._rate_to_encoder_tick_rate(self.config.max_velocity),
      acceleration_ticks_per_second_squared=self._rate_to_encoder_tick_rate(
        self.config.max_acceleration
      ),
      move_current_percent=self.config.moving_current_percentage or None,
      hold_current_percent=self.config.holding_current_percentage or None,
    )

  async def _initialize(self) -> None:
    """Replay the vendor's per-motor initialization configuration."""
    self._initialized = False
    self._supports_accurate_encoder_index = False
    motor_controller_firmware_version = await self.motor.request_motor_controller_firmware_version()
    await self.motor._terminate()
    if motor_controller_firmware_version >= (7, 12):
      special_mode_response = await self.motor.send_command(f"{_EZ_SET_SPECIAL_MODE}32")
      if not special_mode_response.ok:
        raise CeligoError(
          f"motor {self.axis_index} special-mode initialization failed "
          f"(code {special_mode_response.error_code})"
        )

    profile = self._motion_profile()
    command_tokens = (
      f"{_EZ_SET_POSITIVE_DIRECTION}{0 if self.config.default_positive_direction else 1}"
      f"{_EZ_SET_POLARITY}{self.config.limit_polarity}"
      f"{_EZ_SET_MOVE_CURRENT}{self.config.moving_current_percentage}"
      f"{_EZ_SET_HOLD_CURRENT}{self.config.holding_current_percentage}"
      f"{_EZ_SET_ENCODER_RATIO}{round(self.config.encoder_to_motor_tick_ratio * 1000)}"
    )
    if self.config.mode_enable_position_correction:
      command_tokens += (
        f"{_EZ_SET_OVERLOAD_TIMEOUT}{self.config.moving_overload_limit}"
        f"{_EZ_SET_COARSE_WINDOW}{self.config.coarse_position_error_window}"
        f"{_EZ_SET_FINE_WINDOW}{self.config.fine_position_error_window}"
        f"{_EZ_SET_INTEGRATION_PERIOD}{self.config.gain}"
      )
    command_tokens += (
      f"{_EZ_SET_VELOCITY}{profile.velocity_ticks_per_second}"
      f"{_EZ_SET_ACCELERATION}{profile.acceleration_ticks_per_second_squared}"
      f"{_EZ_SET_RESPONSE_TIME}{self.config.motor_response_time}"
    )
    response: Optional[_EZResponse] = None
    last_error: Optional[CeligoError] = None
    for attempt in range(5):
      try:
        response = await self.motor.send_command(command_tokens)
        if response.ok:
          break
        last_error = CeligoError(
          f"motor {self.axis_index} initialization failed (code {response.error_code})"
        )
      except CeligoError as exc:
        last_error = exc
      if attempt < 4:
        await asyncio.sleep(0.1)
    if response is None or not response.ok:
      raise CeligoError(
        f"motor {self.axis_index} initialization failed after five attempts"
      ) from last_error

    await self.motor._set_mode(self._motor_mode(enable_position_correction=False))
    if self.config.s_curve_support:
      await self.motor._set_parameter(
        _EZ_SET_S_CURVE,
        self.config.max_s_acceleration,
        "S-curve setup",
      )
    self._supports_accurate_encoder_index = motor_controller_firmware_version >= (7, 16)
    self._initialized = True

  async def request_encoder_ticks(self) -> int:
    return await self.motor.request_encoder_ticks()

  async def request_encoder_ratio(self) -> float:
    return await self.motor.request_encoder_ratio()

  async def request_limit_flags(self) -> int:
    """Read and polarity-correct the motor's opto/limit input flags."""
    response = await self.motor.send_command(
      f"{_EZ_QUERY}{_EZ_QUERY_FLAGS}",
      execute=False,
    )
    if not response.ok:
      raise CeligoError(f"motor {self.axis_index} limit query failed (code {response.error_code})")
    flags = int(response.response_text) & _LIMIT_ALL
    if self.config.limit_polarity == 1:
      flags = (~flags) & _LIMIT_ALL
    return flags

  async def request_is_negative_limit_active(self) -> bool:
    """Return whether the negative-travel opto input is active."""
    return bool(await self.request_limit_flags() & _LIMIT_OPTO_1)

  async def request_is_positive_limit_active(self) -> bool:
    """Return whether the positive-travel opto input is active."""
    return bool(await self.request_limit_flags() & _LIMIT_OPTO_2)

  async def _restore_homing_configuration(self) -> None:
    await self.motor._set_parameter(
      _EZ_SET_BACKLASH,
      self.config.backlash_compensation,
      "backlash restore",
    )
    if self.config.s_curve_support:
      await self.motor._set_parameter(
        _EZ_SET_S_CURVE,
        self.config.max_s_acceleration,
        "S-curve restore",
      )
    await self.motor._set_mode(self._motor_mode())

  async def _move_homing_relative_ticks(
    self,
    positive: bool,
    distance_ticks: int,
    velocity_ticks_per_second: int,
  ) -> int:
    if distance_ticks <= 0:
      raise ValueError("homing distance must be positive")
    acceleration = self._rate_to_encoder_tick_rate(self.config.max_acceleration)
    direction = _EZ_MOVE_POSITIVE if positive else _EZ_MOVE_NEGATIVE
    response = await self.motor.send_command(
      f"{_EZ_SET_ACCELERATION}{acceleration}"
      f"{_EZ_SET_VELOCITY}{velocity_ticks_per_second}"
      f"{direction}{distance_ticks}"
    )
    if not response.ok:
      raise CeligoError(f"motor {self.axis_index} homing move failed (code {response.error_code})")
    timeout = max(
      self._controller.move_timeout,
      distance_ticks / max(1, velocity_ticks_per_second) + 2.0,
    )
    return await self.motor.wait_until_ready(timeout)

  async def _home_to_encoder_index(
    self,
    search_distance_ticks: int,
    velocity_ticks_per_second: int,
    special_encoder_mode: int,
    timeout: Optional[float] = None,
    restore_motor_mode: Optional[int] = None,
  ) -> int:
    await self.motor._set_mode(0)
    try:
      acceleration = self._rate_to_encoder_tick_rate(self.config.max_acceleration)
      response = await self.motor.send_command(
        f"{_EZ_SET_ACCELERATION}{acceleration}"
        f"{_EZ_SET_VELOCITY}{velocity_ticks_per_second}"
        f"{_EZ_SET_SPECIAL_MODE}{special_encoder_mode}"
        f"{_EZ_HOME}{search_distance_ticks}"
      )
      if not response.ok:
        raise CeligoError(f"motor {self.axis_index} index home failed (code {response.error_code})")
      return await self.motor.wait_until_ready(timeout)
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self.motor._terminate())
      raise
    finally:
      selected_mode = self._motor_mode() if restore_motor_mode is None else restore_motor_mode
      await complete_cleanup(self.motor._set_mode(selected_mode))

  async def move_to_ticks(
    self,
    target_encoder_ticks: int,
    velocity_ticks_per_second: Optional[int] = None,
    arrival_tolerance_ticks: Optional[int] = None,
  ) -> int:
    """Move to an encoder target and verify the settled position."""
    selected_velocity = (
      self._rate_to_encoder_tick_rate(self.config.max_velocity)
      if velocity_ticks_per_second is None
      else velocity_ticks_per_second
    )
    if selected_velocity <= 0:
      raise ValueError("velocity_ticks_per_second must be positive")
    acceleration = self._rate_to_encoder_tick_rate(self.config.max_acceleration)
    temporary_hold_current = min(50, self.config.moving_current_percentage)
    if arrival_tolerance_ticks is not None and arrival_tolerance_ticks < 0:
      raise ValueError("arrival_tolerance_ticks must be non-negative")
    selected_tolerance = (
      self.config.fine_position_error_window
      if arrival_tolerance_ticks is None
      else arrival_tolerance_ticks
    )
    last_position: Optional[int] = None
    for attempt in range(3):
      try:
        response = await self.motor.send_command(
          f"{_EZ_SET_HOLD_CURRENT}{temporary_hold_current}"
          f"{_EZ_SET_ACCELERATION}{acceleration}"
          f"{_EZ_SET_VELOCITY}{selected_velocity}"
          f"{_EZ_MOVE_ABSOLUTE}{target_encoder_ticks}"
        )
        if not response.ok:
          raise CeligoError(f"motor {self.axis_index} move failed (code {response.error_code})")
        last_position = await self.motor.wait_until_ready()
      except BaseException as exc:
        with contextlib.suppress(Exception):
          await complete_cleanup(
            self.motor._set_parameter(
              _EZ_SET_HOLD_CURRENT,
              self.config.holding_current_percentage,
              "hold-current restore",
            )
          )
        if isinstance(exc, (CeligoError, TimeoutError)) and attempt < 2:
          continue
        raise
      await complete_cleanup(
        self.motor._set_parameter(
          _EZ_SET_HOLD_CURRENT,
          self.config.holding_current_percentage,
          "hold-current restore",
        )
      )
      if abs(last_position - target_encoder_ticks) <= selected_tolerance:
        return last_position
    raise CeligoError(
      f"motor {self.axis_index} stopped at {last_position}, target "
      f"{target_encoder_ticks}, tolerance {selected_tolerance}"
    )


class LinearAxis(Axis):
  """A configured X, Y, or Z axis with millimeter movement and homing."""

  config: LinearAxisConfig

  def __init__(
    self,
    controller: MotorController,
    name: LinearAxisName,
    config: LinearAxisConfig,
  ) -> None:
    super().__init__(controller, name, config)
    self._has_position_reference = False

  @property
  def has_position_reference(self) -> bool:
    """Whether this process knows the axis position relative to its physical datum."""
    return self._has_position_reference

  async def _initialize(self) -> None:
    """Forget any process-local datum and replay the motor configuration."""
    self._has_position_reference = False
    await super()._initialize()

  def _rate_to_encoder_tick_rate(self, configured_rate: float) -> int:
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {self.name} has invalid mm_per_encoder_tick")
    return super()._rate_to_encoder_tick_rate(configured_rate / self.config.mm_per_encoder_tick)

  def mm_to_encoder_ticks(self, position_mm: float) -> int:
    """Convert an absolute stage position in millimeters to encoder ticks."""
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {self.name} has invalid mm_per_encoder_tick")
    if not math.isfinite(position_mm):
      raise ValueError("position_mm must be finite")
    direction = -1.0 if self.config.invert_axis_direction else 1.0
    return round(
      (position_mm * direction + self.config.home_offset) / self.config.mm_per_encoder_tick
    )

  def encoder_ticks_to_mm(self, encoder_ticks: int) -> float:
    """Convert an absolute encoder position to stage millimeters."""
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {self.name} has invalid mm_per_encoder_tick")
    direction = -1.0 if self.config.invert_axis_direction else 1.0
    return (encoder_ticks * self.config.mm_per_encoder_tick - self.config.home_offset) * direction

  async def request_position(self) -> float:
    """Read the current axis position in millimeters."""
    return self.encoder_ticks_to_mm(await self.request_encoder_ticks())

  def encoder_bounds(self) -> Tuple[int, int]:
    """Return the configured encoder bounds in ascending order."""
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {self.name} has invalid mm_per_encoder_tick")
    if self.config.max_position <= self.config.min_position:
      raise CeligoError(f"axis {self.name} has invalid configured position bounds")
    endpoints = (
      self.mm_to_encoder_ticks(self.config.min_position),
      self.mm_to_encoder_ticks(self.config.max_position),
    )
    return min(endpoints), max(endpoints)

  def _validate_target(self, target_encoder_ticks: int) -> None:
    minimum_ticks, maximum_ticks = self.encoder_bounds()
    if not minimum_ticks <= target_encoder_ticks <= maximum_ticks:
      raise CeligoError(
        f"axis {self.name} target {target_encoder_ticks} is outside configured "
        f"encoder range {minimum_ticks}..{maximum_ticks}"
      )

  async def move_to_ticks(
    self,
    target_encoder_ticks: int,
    velocity_ticks_per_second: Optional[int] = None,
    arrival_tolerance_ticks: Optional[int] = None,
  ) -> int:
    if not self.has_position_reference:
      raise CeligoError(
        f"axis {self.name} has no position reference; call await "
        f"celigo.{self.name}_axis.home() before moving it"
      )
    if (
      arrival_tolerance_ticks is not None
      and arrival_tolerance_ticks > self.config.fine_position_error_window
    ):
      raise CeligoError("requested tolerance exceeds the configured fine-position window")
    self._validate_target(target_encoder_ticks)
    return await super().move_to_ticks(
      target_encoder_ticks,
      velocity_ticks_per_second=velocity_ticks_per_second,
      arrival_tolerance_ticks=arrival_tolerance_ticks,
    )

  async def move_to(
    self,
    position_mm: float,
    tolerance_mm: Optional[float] = None,
  ) -> float:
    """Move to an absolute position in millimeters."""
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"axis {self.name} has invalid mm_per_encoder_tick")
    low_mm, high_mm = sorted((self.config.min_position, self.config.max_position))
    if not low_mm <= position_mm <= high_mm:
      raise CeligoError(
        f"axis {self.name} target {position_mm:g} mm is outside configured range "
        f"{low_mm:g}..{high_mm:g} mm"
      )
    tolerance_ticks = None
    if tolerance_mm is not None:
      if tolerance_mm < 0:
        raise ValueError("tolerance_mm must be non-negative")
      tolerance_ticks = round(tolerance_mm / self.config.mm_per_encoder_tick)
    settled_ticks = await self.move_to_ticks(
      self.mm_to_encoder_ticks(position_mm),
      arrival_tolerance_ticks=tolerance_ticks,
    )
    return self.encoder_ticks_to_mm(settled_ticks)

  async def assume_homed(self) -> int:
    """Adopt an in-range encoder position from software that already homed the axis.

    This cannot prove that encoder zero matches the physical datum. Normal workflows
    should call :meth:`home`.
    """
    position = await self.request_encoder_ticks()
    self._validate_target(position)
    await self.motor._set_mode(self._motor_mode())
    self._has_position_reference = True
    return position

  async def home(self) -> int:
    """Home this axis with its configured vendor algorithm."""
    supported = {
      "Normal",
      "Normal_Accurate",
      "NormalWithHardstopCheck",
      "NormalWithHardstopCheck_Accurate",
    }
    if self.config.home_type not in supported:
      raise CeligoError(f"axis {self.name!r} has unsupported home type {self.config.home_type!r}")
    if not self.config.mode_enable_limits or not self.config.negative_limit:
      raise CeligoError(f"axis {self.name!r} homing requires a configured negative limit")
    if self.config.homing_short_move <= 0:
      raise CeligoError(f"axis {self.name!r} has an invalid homing backoff distance")
    if not self.is_initialized:
      await self._initialize()

    self._has_position_reference = False
    homing_motor_mode = self._motor_mode(enable_position_correction=False)
    maximum_velocity = self._rate_to_encoder_tick_rate(self.config.max_velocity)
    homing_velocity = self._rate_to_encoder_tick_rate(self.config.homing_velocity)
    index_velocity = self._rate_to_encoder_tick_rate(self.config.index_velocity)

    async def terminate_and_restore() -> None:
      with contextlib.suppress(Exception):
        await complete_cleanup(self.motor._terminate())
      with contextlib.suppress(Exception):
        await complete_cleanup(self._restore_homing_configuration())

    try:
      await self.motor._set_mode(homing_motor_mode)
      if self.config.s_curve_support:
        await self.motor._set_parameter(_EZ_SET_S_CURVE, 0, "S-curve disable")
      await self.motor._set_parameter(_EZ_SET_BACKLASH, 0, "backlash disable")

      initial_encoder_ticks = await self.request_encoder_ticks()
      await self._move_homing_relative_ticks(True, 5, maximum_velocity)
      if await self.request_encoder_ticks() == initial_encoder_ticks:
        await self._move_homing_relative_ticks(False, 10, maximum_velocity)
        if await self.request_encoder_ticks() == initial_encoder_ticks:
          raise CeligoError(f"axis {self.name!r} encoder did not respond to the homing probe")

      await self._move_homing_relative_ticks(False, 25000, homing_velocity)
      if not (await self.request_limit_flags() & _LIMIT_OPTO_1):
        raise CeligoError(
          f"axis {self.name!r} stopped without activating its negative-limit sensor"
        )
      await asyncio.sleep(0.05)
      await self._move_homing_relative_ticks(
        True,
        self.config.homing_short_move,
        homing_velocity,
      )
      if await self.request_limit_flags() & _LIMIT_OPTO_1:
        raise CeligoError(f"axis {self.name!r} negative-limit sensor did not clear after backoff")
      await asyncio.sleep(0.05)

      if self.config.home_type.startswith("NormalWithHardstopCheck"):
        search_distance_ticks = 25000
        special_mode = _EZ_SPECIAL_ENCODER_NO_INDEX
      else:
        search_distance_ticks = self.config.homing_short_move * 2
        special_mode = (
          _EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE
          if self.config.home_type == "Normal_Accurate" and self._supports_accurate_encoder_index
          else _EZ_SPECIAL_ENCODER_WITH_INDEX
        )

      await self._home_to_encoder_index(
        search_distance_ticks,
        index_velocity,
        special_mode,
        timeout=max(
          self._controller.move_timeout,
          search_distance_ticks / max(1, index_velocity) + 2.0,
        ),
        restore_motor_mode=homing_motor_mode,
      )
      await super().move_to_ticks(
        0,
        velocity_ticks_per_second=maximum_velocity,
      )
      await self._restore_homing_configuration()
      settled_ticks = await super().move_to_ticks(
        self.mm_to_encoder_ticks(self.config.min_position)
      )
      self._has_position_reference = True
      return settled_ticks
    except BaseException:
      self._has_position_reference = False
      await terminate_and_restore()
      raise

  async def _move_relative_to_limit(
    self,
    distance_ticks: int,
    move_current_percent: Optional[int] = None,
  ) -> None:
    """Move a trusted axis relatively toward a limit."""
    if distance_ticks == 0:
      raise ValueError("relative move distance must be non-zero")
    if not self.has_position_reference:
      raise CeligoError(f"axis {self.name} has no position reference")
    profile = self._motion_profile()
    selected_current = (
      profile.move_current_percent if move_current_percent is None else move_current_percent
    )
    command_tokens = ""
    if selected_current is not None:
      command_tokens += f"{_EZ_SET_MOVE_CURRENT}{selected_current}"
    if profile.hold_current_percent is not None:
      command_tokens += f"{_EZ_SET_HOLD_CURRENT}{profile.hold_current_percent}"
    command_tokens += (
      f"{_EZ_SET_VELOCITY}{profile.velocity_ticks_per_second}"
      f"{_EZ_SET_ACCELERATION}{profile.acceleration_ticks_per_second_squared}"
      f"{_EZ_MOVE_POSITIVE if distance_ticks > 0 else _EZ_MOVE_NEGATIVE}"
      f"{abs(distance_ticks)}"
    )
    response = await self.motor.send_command(command_tokens)
    if not response.ok:
      raise CeligoError(f"axis {self.name} relative move error (code {response.error_code})")
    await self.motor.wait_until_ready()

  def _limit_move_distance_ticks(self) -> int:
    """Return a relative distance guaranteed to exceed configured travel."""
    if self.config.mm_per_encoder_tick <= 0:
      raise CeligoError(f"Cannot derive {self.name.upper()} limit move without axis configuration")
    configured_travel_ticks = (
      abs(self.config.max_position - self.config.min_position) / self.config.mm_per_encoder_tick
    )
    return math.ceil(configured_travel_ticks) + abs(self.config.homing_short_move)


class FilterWheel(Axis):
  """A configured rotary wheel with a learned physical-position-one datum."""

  config: FilterWheelConfig

  def __init__(
    self,
    controller: MotorController,
    component_name: str,
    config: FilterWheelConfig,
  ) -> None:
    super().__init__(controller, component_name, config)
    self._home_encoder_ticks: Optional[int] = None

  @property
  def has_position_reference(self) -> bool:
    """Whether this process has homed the wheel to physical position one."""
    return self._home_encoder_ticks is not None

  async def _initialize(self) -> None:
    """Forget the learned wheel datum and replay the motor configuration."""
    self._home_encoder_ticks = None
    await super()._initialize()

  def _ticks_per_position(self) -> int:
    if self.config.number_of_filters <= 0 or self.config.encoder_ticks_per_revolution <= 0:
      raise CeligoError(f"{self.name} wheel geometry is invalid")
    if self.config.encoder_ticks_per_revolution % self.config.number_of_filters != 0:
      raise CeligoError(f"{self.name} encoder ticks/revolution is not divisible by filter count")
    return self.config.encoder_ticks_per_revolution // self.config.number_of_filters

  async def home(self) -> int:
    """Reference the encoder index and locate physical wheel position one."""
    if not self.is_initialized:
      await self._initialize()
    ticks_per_position = self._ticks_per_position()
    self._home_encoder_ticks = None
    search_distance_ticks = round(ticks_per_position * 1.2)
    index_velocity = self._rate_to_encoder_tick_rate(self.config.index_velocity)
    index_mode = (
      _EZ_SPECIAL_ENCODER_WITH_INDEX_ACCURATE
      if self._supports_accurate_encoder_index
      else _EZ_SPECIAL_ENCODER_WITH_INDEX
    )
    index_timeout = max(
      self._controller.move_timeout,
      abs(search_distance_ticks) / max(1, abs(index_velocity)) + 1.0,
    )
    last_error: Optional[Exception] = None
    for _ in range(3):
      try:
        await self._home_to_encoder_index(
          search_distance_ticks,
          index_velocity,
          index_mode,
          timeout=index_timeout,
        )
        last_error = None
        break
      except (CeligoError, TimeoutError) as exc:
        last_error = exc
    if last_error is not None:
      await self._initialize()
      raise CeligoError(f"Failed to find encoder index for {self.name}") from last_error

    target_encoder_ticks = round(self.config.home_offset)
    homing_velocity = self._rate_to_encoder_tick_rate(self.config.homing_velocity)
    try:
      for physical_position in range(1, self.config.number_of_filters + 1):
        await self.move_to_ticks(
          target_encoder_ticks,
          velocity_ticks_per_second=homing_velocity,
        )
        if await self.request_limit_flags() & _LIMIT_OPTO_1:
          self._home_encoder_ticks = target_encoder_ticks
          return target_encoder_ticks
        if physical_position < self.config.number_of_filters:
          target_encoder_ticks += ticks_per_position
    except BaseException:
      with contextlib.suppress(Exception):
        await complete_cleanup(self._initialize())
      raise
    await self._initialize()
    raise CeligoError(f"Opto1 sensor was not active at any {self.name} position")

  async def move_to(self, logical_position: int) -> int:
    """Move to a configured logical position by the shortest equivalent path."""
    if self._home_encoder_ticks is None:
      raise CeligoError(f"{self.name} home position is unknown; home the wheel first")
    logical_to_physical = {
      entry.logical_number: entry.physical_number for entry in self.config.filter_map
    }
    try:
      physical_position = logical_to_physical[logical_position]
    except KeyError as exc:
      raise CeligoError(
        f"Logical position {logical_position} is not configured for {self.name}"
      ) from exc

    ticks_per_position = self._ticks_per_position()
    canonical_target = self._home_encoder_ticks + (physical_position - 1) * ticks_per_position
    current_encoder_ticks = await self.request_encoder_ticks()
    revolutions = math.ceil(
      (current_encoder_ticks - canonical_target) / self.config.encoder_ticks_per_revolution - 0.5
    )
    return await self.move_to_ticks(
      canonical_target + revolutions * self.config.encoder_ticks_per_revolution
    )


class MagnificationChanger(FilterWheel):
  """Objective wheel that also owns the active magnification calibration state."""

  def __init__(
    self,
    controller: MotorController,
    config: FilterWheelConfig,
    instrument_config: CeligoConfig,
  ) -> None:
    super().__init__(controller, "magnification", config)
    self._instrument_config = instrument_config

  async def move_to(self, logical_position: int) -> int:
    """Select a supported magnification and make its calibrations active."""
    if logical_position not in (3, 5, 10, 20):
      raise CeligoError(f"Unsupported magnification {logical_position}X")
    if logical_position not in self._instrument_config.channels_by_magnification:
      raise CeligoError(f"No illumination-channel calibration is loaded for {logical_position}X")
    settled_ticks = await super().move_to(logical_position)
    self._instrument_config.magnification = logical_position
    return settled_ticks
