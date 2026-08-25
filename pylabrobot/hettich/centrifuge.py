"""Serial driver for Generation 2 Hettich robotic centrifuges."""

import asyncio
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Mapping, Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

EOT = 0x04
STX = 0x02
ETX = 0x03
ENQ = 0x05
ACK = 0x06
NAK = 0x15

ENQUIRY_REPLY_LENGTH = 14
COMMAND_REPLY_LENGTH = 2

# Protocol source: Hettich document AH5680-01EN.
# https://www.hettweb.com/wp-content/uploads/2019/09/OM-ROBOTIC-CENTRIFUGE-COMMUNICATION-PARAMETERS-AH5680-01EN.pdf
SIOF_PARAMETER = "00685"
GENERATION_PARAMETER = "00600"
DEVICE_TYPE_PARAMETER = "00537"
SOFTWARE_VERSION_PARAMETER = "00636"
RUN_TIME_PARAMETER = "00601"
ACTUAL_RUN_TIME_PARAMETER = "00602"
SPEED_PARAMETER = "00603"
ACTUAL_SPEED_PARAMETER = "00604"
MAXIMUM_SPEED_PARAMETER = "00605"
MAXIMUM_RUN_UP_TIME_PARAMETER = "00614"
MAXIMUM_RUN_DOWN_TIME_PARAMETER = "00616"
ACTIVATE_PARAMETERS_COMMAND = "00522"
SPIN_COMMAND = "00521"
TARGET_POSITION_PARAMETER = "00524"
POSITION_COMMAND = "00526"
HATCH_STATUS_PARAMETER = "00528"
PROGRAM_COMMAND = "00523"
STATUS_1_PARAMETER = "00634"
STATUS_2_PARAMETER = "00635"
TEMPERATURE_PARAMETER = "00619"

MINIMUM_SPEED = 50
MAXIMUM_DURATION = 59_999
DEFAULT_TIMEOUT_MARGIN = 30
GENERATION_2_IDENTIFICATION = 0x1234

_VALID_ADDRESSES = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]")

_SIOF_MESSAGES = {
  0: "power on after reset or mains interruption",
  1: "serial parity error",
  2: "maximum allowed rotor cycles passed",
  3: "wrong BCC checksum",
  4: "framing error (wrong STX, ETX, ENQ, or '=')",
  5: "wrong or unknown parameter",
  6: "modification not permitted (read-only parameter)",
  7: "improper value or command not allowed",
}

_KNOWN_DEVICE_TYPES = {
  0xE800: "MIKRO 220 POS",
  0xC901: "ROTANTA 460 R POS",
  0xC000: "ROTANTA 460",
  0x8800: "ROTINA 380 POS",
  0x8901: "ROTINA 380 R POS",
  0x8904: "ROTINA 380",
  0xE801: "MIKRO 220 POS",
}

SpinPhase = Literal["standstill", "accelerating", "centrifuging", "braking", "unknown"]
KeyLockState = Literal["teach", "remote", "middle", "software_lock_4", "software_lock_5", "unknown"]
PositioningSpeed = Literal["slow", "fast"]


@dataclass(frozen=True)
class RotorSpecification:
  """Manufacturer limits for a Hettich robotic centrifuge rotor.

  Attributes:
    catalog_number: Hettich rotor catalog number.
    positions: Number of tube positions.
    maximum_volume: Maximum tube volume in microliters.
    maximum_speed: Maximum rotor speed in rpm.
    maximum_rcf: Maximum relative centrifugal force in multiples of gravity.
  """

  catalog_number: str
  positions: int
  maximum_volume: int
  maximum_speed: int
  maximum_rcf: int

  def rcf_at_speed(self, speed: int) -> float:
    """Return the RCF produced at ``speed`` in rpm."""
    if not 0 <= speed <= self.maximum_speed:
      raise ValueError(f"speed must be 0..{self.maximum_speed} rpm")
    return self.maximum_rcf * (speed / self.maximum_speed) ** 2

  def speed_for_rcf(self, rcf: float) -> int:
    """Return the nearest speed in rpm that produces ``rcf``."""
    if not 0 <= rcf <= self.maximum_rcf:
      raise ValueError(f"rcf must be 0..{self.maximum_rcf}")
    return round(self.maximum_speed * math.sqrt(rcf / self.maximum_rcf))


# Source: https://www.hettichlab.com/products/centrifuges/automated-centrifuges/mikro-220-robotic/
MIKRO_220_ROBOTIC_ROTORS: Mapping[str, RotorSpecification] = {
  "2334": RotorSpecification(
    catalog_number="2334",
    positions=24,
    maximum_volume=2_000,
    maximum_speed=13_000,
    maximum_rcf=18_327,
  ),
  "2394": RotorSpecification(
    catalog_number="2394",
    positions=24,
    maximum_volume=2_000,
    maximum_speed=13_000,
    maximum_rcf=18_516,
  ),
}


@dataclass(frozen=True)
class _ModelConfiguration:
  """Exact protocol identity and capabilities for one centrifuge model."""

  name: str
  device_type_codes: frozenset[int]
  rotor_specifications: Mapping[str, RotorSpecification]
  physically_verified: bool


_MIKRO_220_ROBOTIC_CONFIGURATION = _ModelConfiguration(
  name="MIKRO 220 POS",
  device_type_codes=frozenset((0xE800, 0xE801)),
  rotor_specifications=MIKRO_220_ROBOTIC_ROTORS,
  physically_verified=True,
)

_ROTANTA_460_ROBOTIC_CONFIGURATION = _ModelConfiguration(
  name="ROTANTA 460 R POS",
  device_type_codes=frozenset((0xC901,)),
  rotor_specifications={},
  physically_verified=False,
)

_ROTINA_380_ROBOTIC_CONFIGURATION = _ModelConfiguration(
  name="ROTINA 380 POS",
  device_type_codes=frozenset((0x8800,)),
  rotor_specifications={},
  physically_verified=False,
)

_ROTINA_380_R_ROBOTIC_CONFIGURATION = _ModelConfiguration(
  name="ROTINA 380 R POS",
  device_type_codes=frozenset((0x8901,)),
  rotor_specifications={},
  physically_verified=False,
)


@dataclass(frozen=True)
class CentrifugeStatus:
  """Decoded values from Hettich parameters 00634 and 00635."""

  phase: SpinPhase
  can_start: bool
  status_changed: bool
  error_number: Optional[int]
  program_number: Optional[int]
  rotor_number: int
  key_lock: KeyLockState
  key_lock_code: int
  lid_closed: bool
  rotor_cycle_counter_enabled: bool
  maximum_rotor_cycles_exceeded: bool
  rotor_cycle_limit_confirmed: bool
  rotor_changed: bool
  no_rotor: bool


@dataclass(frozen=True)
class HatchStatus:
  """Decoded positioning and hatch values from Hettich parameter 00528."""

  hatch_open: bool
  hatch_closed: bool
  lid_lock_closed: bool
  hatch_moving: bool
  hatch_opening: bool
  hatch_closing: bool
  hatch_timeout: bool
  magnetic_brake_implemented: bool
  magnetic_brake_active: bool
  position_reached: bool
  positioning_active: bool
  rotor_moving: bool
  positioning_timeout: bool
  positioning_error: bool


class HettichCentrifugeError(Exception):
  """Base exception raised by a Hettich robotic centrifuge."""


class HettichCommunicationError(HettichCentrifugeError):
  """The centrifuge returned no response or a malformed response."""


class HettichCommandError(HettichCentrifugeError):
  """The centrifuge rejected a parameter or command."""

  def __init__(self, parameter: str, siof: int) -> None:
    """Describe a rejected parameter using the device's SIOF fault bits."""
    self.parameter = parameter
    self.siof = siof
    messages = [_SIOF_MESSAGES[bit] for bit in range(8) if siof & (1 << bit)]
    reason = ", ".join(messages) if messages else "no SIOF reason bit was set"
    super().__init__(f"Hettich parameter {parameter} was rejected: {reason} (SIOF=0x{siof:02X})")


class HettichRoboticCentrifuge(ABC):
  """Shared Generation 2 protocol for a model-specific Hettich centrifuge.

  The protocol is shared by Hettich robotic centrifuges with the C control panel,
  including the MIKRO 220 POS and ROTANTA 460 R POS. The connection uses 9600
  baud, 7 data bits, even parity, one stop bit, and no flow control. Values are
  four hexadecimal ASCII digits inside addressed ENQUIRY and SELECT telegrams.

  ``setup()`` only reads the serial fault register and identification fields; it
  does not move the rotor or hatch. Motion methods verify the reported machine
  state before transmitting a command.

  Instantiate a concrete model class rather than this abstract protocol class.
  Each subclass declares exact device-type codes and model-specific capabilities.
  """

  @classmethod
  @abstractmethod
  def _configuration(cls) -> _ModelConfiguration:
    """Return the exact identity and capabilities declared by this model class."""

  def __init__(
    self,
    port: str,
    address: str = "]",
    timeout: float = 0.2,
    retries: int = 3,
    poll_interval: float = 0.5,
    rotor_catalog_number: Optional[str] = None,
  ) -> None:
    """Create a Hettich robotic centrifuge connection.

    Args:
      port: Serial port connected to the centrifuge.
      address: One-character Hettich bus address. The factory default is ``]``.
      timeout: Per-read timeout in seconds. The manual specifies a maximum
        response time of 150 ms.
      retries: Total transmission attempts after a timeout or invalid reply.
      poll_interval: Delay between state requests while waiting for motion.
      rotor_catalog_number: Hettich catalog number for the installed rotor. The
        concrete model class defines which catalog numbers are supported.
    """
    if address not in _VALID_ADDRESSES:
      raise ValueError("address must be one of A-Z, [, \\, or ]")
    if timeout < 0.15:
      raise ValueError("timeout must be at least 0.15 seconds")
    if retries < 1:
      raise ValueError("retries must be at least 1")
    if poll_interval < 0:
      raise ValueError("poll_interval must be non-negative")
    configuration = self._configuration()
    if (
      rotor_catalog_number is not None
      and rotor_catalog_number not in configuration.rotor_specifications
    ):
      supported = ", ".join(configuration.rotor_specifications)
      if supported:
        raise ValueError(f"unsupported rotor catalog number; expected one of {supported}")
      raise ValueError(f"rotor specifications are not available for {configuration.name}")

    self.address = address
    self.retries = retries
    self.poll_interval = poll_interval
    self.rotor_specification = (
      configuration.rotor_specifications[rotor_catalog_number]
      if rotor_catalog_number is not None
      else None
    )
    self.io = Serial(
      human_readable_device_name=f"Hettich {configuration.name}",
      port=port,
      baudrate=9600,
      bytesize=7,
      parity="E",
      stopbits=1,
      timeout=timeout,
      write_timeout=timeout,
      rtscts=False,
      dsrdtr=False,
      xonxoff=False,
    )
    self._transaction_lock = asyncio.Lock()
    self.device_type_code: Optional[int] = None
    self.device_type: Optional[str] = None
    self.software_version: Optional[str] = None

  async def setup(self) -> None:
    """Open the port and verify a Generation 2 Hettich centrifuge without moving it."""
    await self.io.setup()
    try:
      await self.io.reset_input_buffer()
      await self.io.reset_output_buffer()

      siof = await self._enquire_parameter(SIOF_PARAMETER, allow_nak=False)
      if siof:
        logger.info("[Hettich %s] cleared startup SIOF=0x%02X", self.io.port, siof)

      generation = await self._enquire_parameter(GENERATION_PARAMETER)
      if generation != GENERATION_2_IDENTIFICATION:
        raise HettichCentrifugeError(
          "The connected centrifuge did not identify as Hettich Generation 2 "
          f"(received 0x{generation:04X})"
        )

      self.device_type_code = await self._enquire_parameter(DEVICE_TYPE_PARAMETER)
      configuration = self._configuration()
      if self.device_type_code not in configuration.device_type_codes:
        connected_type = _KNOWN_DEVICE_TYPES.get(
          self.device_type_code, f"unknown type 0x{self.device_type_code:04X}"
        )
        expected_codes = ", ".join(
          f"0x{code:04X}" for code in sorted(configuration.device_type_codes)
        )
        raise HettichCentrifugeError(
          f"{type(self).__name__} requires {configuration.name} ({expected_codes}), "
          f"but the connected centrifuge reports {connected_type} "
          f"(0x{self.device_type_code:04X})"
        )
      self.device_type = configuration.name
      software = await self._enquire_parameter(SOFTWARE_VERSION_PARAMETER)
      software_digits = f"{software:04X}"
      self.software_version = f"{software_digits[:2]}.{software_digits[2:]}"

      if not configuration.physically_verified:
        logger.warning(
          "%s support for %s has not been physically verified in PyLabRobot",
          type(self).__name__,
          self.device_type,
        )

      logger.info(
        "[Hettich %s] connected: %s, software %s, address %s",
        self.io.port,
        self.device_type,
        self.software_version,
        self.address,
      )
    except BaseException:
      await self.io.stop()
      raise

  async def stop(self) -> None:
    """Close the serial connection without changing the centrifuge's run state."""
    await self.io.stop()

  @staticmethod
  def _bcc(data: bytes) -> int:
    """Return the XOR block-check character for bytes after STX through ETX."""
    checksum = 0
    for byte in data:
      checksum ^= byte
    return checksum

  def _build_enquiry(self, parameter: str) -> bytes:
    """Build an eight-byte ENQUIRY telegram."""
    self._validate_parameter(parameter)
    return bytes([EOT, ord(self.address)]) + parameter.encode("ascii") + bytes([ENQ])

  def _build_select(self, parameter: str, value: int) -> bytes:
    """Build a 15-byte SELECT telegram with its BCC."""
    self._validate_parameter(parameter)
    if not 0 <= value <= 0xFFFF:
      raise ValueError("parameter value must be 0..65535")
    body = bytes([STX]) + parameter.encode("ascii") + f"={value:04X}".encode("ascii") + bytes([ETX])
    return bytes([EOT, ord(self.address)]) + body + bytes([self._bcc(body[1:])])

  @staticmethod
  def _validate_parameter(parameter: str) -> None:
    """Require a five-digit parameter identifier accepted by the protocol."""
    if len(parameter) != 5 or not parameter.isascii() or not parameter.isdigit():
      raise ValueError("parameter must contain exactly five ASCII digits")

  async def _read_exact(self, length: int) -> bytes:
    """Read exactly ``length`` bytes, raising when the serial timeout expires."""
    data = bytearray()
    while len(data) < length:
      chunk = await self.io.read(length - len(data))
      if not chunk:
        break
      data.extend(chunk)
    if len(data) != length:
      raise HettichCommunicationError(f"expected {length} reply bytes, received {len(data)}")
    return bytes(data)

  def _parse_enquiry_reply(self, reply: bytes, parameter: str) -> int:
    """Validate and decode a 14-byte ENQUIRY reply."""
    if len(reply) != ENQUIRY_REPLY_LENGTH:
      raise HettichCommunicationError(
        f"expected {ENQUIRY_REPLY_LENGTH} reply bytes, received {len(reply)}"
      )
    if reply[0] != ord(self.address):
      raise HettichCommunicationError(
        f"reply address {chr(reply[0])!r} did not match {self.address!r}"
      )
    if reply[1] != STX or reply[7] != ord("=") or reply[12] != ETX:
      raise HettichCommunicationError(f"malformed reply for parameter {parameter}: {reply!r}")
    reply_parameter = reply[2:7].decode("ascii", errors="replace")
    if reply_parameter != parameter:
      raise HettichCommunicationError(
        f"reply parameter {reply_parameter!r} did not match {parameter!r}"
      )
    expected_bcc = self._bcc(reply[2:13])
    if reply[13] != expected_bcc:
      raise HettichCommunicationError(
        f"wrong BCC for parameter {parameter}: received 0x{reply[13]:02X}, "
        f"expected 0x{expected_bcc:02X}"
      )
    raw_value = reply[8:12]
    if any(byte not in b"0123456789ABCDEF" for byte in raw_value):
      raise HettichCommunicationError(f"parameter {parameter} returned a non-hexadecimal value")
    return int(raw_value.decode("ascii"), 16)

  async def _request_enquiry(self, parameter: str) -> Optional[int]:
    """Transmit an ENQUIRY and return its value, or ``None`` for NAK."""
    frame = self._build_enquiry(parameter)
    last_error: Optional[HettichCommunicationError] = None
    for attempt in range(1, self.retries + 1):
      try:
        await self.io.write(frame)
        prefix = await self._read_exact(COMMAND_REPLY_LENGTH)
        if prefix == bytes([ord(self.address), NAK]):
          return None
        reply = prefix + await self._read_exact(ENQUIRY_REPLY_LENGTH - len(prefix))
        return self._parse_enquiry_reply(reply, parameter)
      except HettichCommunicationError as exc:
        last_error = exc
        logger.warning(
          "[Hettich %s] ENQUIRY %s attempt %d/%d failed: %s",
          self.io.port,
          parameter,
          attempt,
          self.retries,
          exc,
        )
      finally:
        await self.io.write(bytes([EOT]))
    assert last_error is not None
    raise HettichCommunicationError(
      f"ENQUIRY {parameter} failed after {self.retries} attempts: {last_error}"
    ) from last_error

  async def _request_select(self, parameter: str, value: int) -> bool:
    """Transmit a SELECT and return whether the centrifuge acknowledged it."""
    frame = self._build_select(parameter, value)
    last_error: Optional[HettichCommunicationError] = None
    for attempt in range(1, self.retries + 1):
      try:
        await self.io.write(frame)
        reply = await self._read_exact(COMMAND_REPLY_LENGTH)
        if reply[0] != ord(self.address):
          raise HettichCommunicationError(
            f"reply address {chr(reply[0])!r} did not match {self.address!r}"
          )
        if reply[1] == ACK:
          return True
        if reply[1] == NAK:
          return False
        raise HettichCommunicationError(f"invalid acknowledgement: {reply!r}")
      except HettichCommunicationError as exc:
        last_error = exc
        logger.warning(
          "[Hettich %s] SELECT %s attempt %d/%d failed: %s",
          self.io.port,
          parameter,
          attempt,
          self.retries,
          exc,
        )
      finally:
        await self.io.write(bytes([EOT]))
    assert last_error is not None
    raise HettichCommunicationError(
      f"SELECT {parameter} failed after {self.retries} attempts: {last_error}"
    ) from last_error

  async def _enquire_parameter(self, parameter: str, allow_nak: bool = True) -> int:
    """Read a parameter while serializing access to the bus."""
    async with self._transaction_lock:
      value = await self._request_enquiry(parameter)
      if value is not None:
        return value
      if not allow_nak or parameter == SIOF_PARAMETER:
        raise HettichCommunicationError(f"ENQUIRY {parameter} was rejected")
      siof = await self._request_enquiry(SIOF_PARAMETER)
      if siof is None:
        raise HettichCommunicationError("SIOF enquiry was rejected after NAK")
      raise HettichCommandError(parameter, siof)

  async def _select_parameter(self, parameter: str, value: int) -> None:
    """Write a parameter and decode SIOF when the centrifuge returns NAK."""
    async with self._transaction_lock:
      acknowledged = await self._request_select(parameter, value)
      if acknowledged:
        return
      siof = await self._request_enquiry(SIOF_PARAMETER)
      if siof is None:
        raise HettichCommunicationError("SIOF enquiry was rejected after NAK")
      raise HettichCommandError(parameter, siof)

  @staticmethod
  def _phase(status_byte: int) -> SpinPhase:
    """Decode the motion phase bits in the low byte of parameter 00634."""
    if status_byte & 0x10:
      return "braking"
    if status_byte & 0x08:
      return "centrifuging"
    if status_byte & 0x04:
      return "accelerating"
    if status_byte & 0x02:
      return "standstill"
    return "unknown"

  @staticmethod
  def _key_lock(code: int) -> KeyLockState:
    """Decode the key-switch or software-lock code from parameter 00635."""
    states: dict[int, KeyLockState] = {
      1: "teach",
      2: "remote",
      3: "middle",
      4: "software_lock_4",
      5: "software_lock_5",
    }
    return states.get(code, "unknown")

  async def request_status(self) -> CentrifugeStatus:
    """Read and decode centrifuge state, program/error, rotor, lid, and key lock."""
    state_1 = await self._enquire_parameter(STATUS_1_PARAMETER)
    if self.poll_interval:
      await asyncio.sleep(min(self.poll_interval, 0.4))
    state_2 = await self._enquire_parameter(STATUS_2_PARAMETER)
    state_byte = state_1 & 0xFF
    program_or_error = state_1 >> 8
    state_2_high = state_2 >> 8
    state_2_low = state_2 & 0xFF
    has_error = bool(program_or_error & 0x80)
    key_lock_code = state_2_low & 0x07
    return CentrifugeStatus(
      phase=self._phase(state_byte),
      can_start=not bool(state_byte & 0x01),
      status_changed=bool(state_byte & 0x80),
      error_number=(program_or_error & 0x7F) if has_error else None,
      program_number=None if has_error else program_or_error,
      rotor_number=(state_2_low >> 4) & 0x0F,
      key_lock=self._key_lock(key_lock_code),
      key_lock_code=key_lock_code,
      lid_closed=bool(state_2_high & 0x02),
      rotor_cycle_counter_enabled=bool(state_2_high & 0x80),
      maximum_rotor_cycles_exceeded=bool(state_2_high & 0x40),
      rotor_cycle_limit_confirmed=bool(state_2_high & 0x20),
      rotor_changed=bool(state_2_high & 0x08),
      no_rotor=bool(state_2_high & 0x04),
    )

  async def request_hatch_status(self) -> HatchStatus:
    """Read and decode hatch and positioning state."""
    value = await self._enquire_parameter(HATCH_STATUS_PARAMETER)
    hatch = value >> 8
    position = value & 0xFF
    return HatchStatus(
      hatch_open=bool(hatch & 0x20),
      hatch_closed=bool(hatch & 0x10),
      lid_lock_closed=bool(hatch & 0x08),
      hatch_moving=bool(hatch & 0x04),
      hatch_opening=bool(hatch & 0x02),
      hatch_closing=bool(hatch & 0x01),
      hatch_timeout=bool(hatch & 0x40),
      magnetic_brake_implemented=bool(hatch & 0x80),
      magnetic_brake_active=bool(position & 0x20),
      position_reached=bool(position & 0x04),
      positioning_active=bool(position & 0x02),
      rotor_moving=bool(position & 0x01),
      positioning_timeout=bool(position & 0x08),
      positioning_error=bool(position & 0x10),
    )

  async def request_speed(self) -> int:
    """Return the actual rotor speed in rpm."""
    return await self._enquire_parameter(ACTUAL_SPEED_PARAMETER)

  async def request_maximum_speed(self) -> int:
    """Return the installed rotor's maximum speed in rpm."""
    return await self._enquire_parameter(MAXIMUM_SPEED_PARAMETER)

  def rcf_at_speed(self, speed: int) -> float:
    """Return RCF at ``speed`` using the configured rotor specification.

    Raises:
      HettichCentrifugeError: No rotor catalog number was supplied at construction.
      ValueError: ``speed`` is outside the rotor's manufacturer limits.
    """
    if self.rotor_specification is None:
      raise HettichCentrifugeError(
        "Set rotor_catalog_number to calculate RCF from the manufacturer rotor table"
      )
    return self.rotor_specification.rcf_at_speed(speed)

  def speed_for_rcf(self, rcf: float) -> int:
    """Return rpm for ``rcf`` using the configured rotor specification.

    Raises:
      HettichCentrifugeError: No rotor catalog number was supplied at construction.
      ValueError: ``rcf`` is outside the rotor's manufacturer limits.
    """
    if self.rotor_specification is None:
      raise HettichCentrifugeError(
        "Set rotor_catalog_number to calculate speed from the manufacturer rotor table"
      )
    return self.rotor_specification.speed_for_rcf(rcf)

  async def request_elapsed_time(self) -> int:
    """Return the current run time in seconds."""
    return await self._enquire_parameter(ACTUAL_RUN_TIME_PARAMETER)

  @staticmethod
  def _require_remote_standstill(status: CentrifugeStatus) -> None:
    """Require remote control, standstill, and no reported centrifuge error."""
    if status.key_lock not in ("remote", "software_lock_4", "software_lock_5"):
      raise HettichCentrifugeError("The key-operated switch must be in LOCK 2 before remote motion")
    if status.error_number is not None:
      raise HettichCentrifugeError(
        f"The centrifuge reports error {status.error_number}; resolve it before motion"
      )
    if status.phase != "standstill":
      raise HettichCentrifugeError(
        f"The centrifuge must be at standstill, but reports {status.phase}"
      )

  @staticmethod
  def _require_positioning_ready(status: CentrifugeStatus) -> None:
    """Require the machine state mandated for hatch and positioning commands."""
    HettichRoboticCentrifuge._require_remote_standstill(status)
    if not status.lid_closed:
      raise HettichCentrifugeError(
        "Close and lock the main centrifuge lid before hatch or rotor positioning motion"
      )

  async def _wait_for_hatch(self, desired: Literal["open", "closed"], timeout: float) -> None:
    """Wait until the hatch reaches ``desired`` or reports a positioning fault."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
      hatch = await self.request_hatch_status()
      if hatch.hatch_timeout or hatch.positioning_timeout or hatch.positioning_error:
        raise HettichCentrifugeError("The centrifuge reported a hatch or positioning error")
      if desired == "open" and hatch.hatch_open:
        return
      if desired == "closed" and hatch.hatch_closed and hatch.lid_lock_closed:
        return
      if loop.time() >= deadline:
        raise TimeoutError(f"Hettich hatch did not reach {desired} within {timeout} seconds")
      await asyncio.sleep(self.poll_interval)

  async def open_hatch(self, timeout: float = 30.0) -> None:
    """Move the loading hatch to the open state and wait for confirmation."""
    hatch = await self.request_hatch_status()
    if hatch.hatch_open:
      return
    self._require_positioning_ready(await self.request_status())
    logger.info("[Hettich %s] opening hatch", self.io.port)
    await self._select_parameter(POSITION_COMMAND, 0x0060)
    await self._wait_for_hatch("open", timeout)

  async def close_hatch(self, timeout: float = 30.0) -> None:
    """Move the loading hatch to the closed state and wait for both closed switches."""
    hatch = await self.request_hatch_status()
    if hatch.hatch_closed and hatch.lid_lock_closed:
      return
    self._require_positioning_ready(await self.request_status())
    logger.info("[Hettich %s] closing hatch", self.io.port)
    await self._select_parameter(POSITION_COMMAND, 0x0070)
    await self._wait_for_hatch("closed", timeout)

  async def move_to_position(
    self,
    position: int,
    speed: PositioningSpeed = "slow",
    timeout: float = 320.0,
  ) -> None:
    """Move a rotor bucket beneath the loading hatch and hold it there.

    Args:
      position: One-based rotor position.
      speed: ``"slow"`` for agitation-sensitive samples or ``"fast"``.
      timeout: Maximum total positioning time. Firmware may make three attempts
        of up to 100 seconds each.
    """
    if speed not in ("slow", "fast"):
      raise ValueError('speed must be "slow" or "fast"')
    current_target = await self._enquire_parameter(TARGET_POSITION_PARAMETER)
    maximum_positions = current_target >> 8
    if not 1 <= position <= maximum_positions:
      raise ValueError(f"position must be 1..{maximum_positions} for the installed rotor")

    hatch = await self.request_hatch_status()
    self._require_positioning_ready(await self.request_status())
    if current_target & 0xFF == position and hatch.position_reached:
      return

    if hatch.rotor_moving:
      if current_target & 0xFF != position:
        raise HettichCentrifugeError(
          "A different rotor positioning move is already active; wait for it to finish"
        )
    else:
      if current_target & 0xFF != position:
        await self._select_parameter(TARGET_POSITION_PARAMETER, (maximum_positions << 8) | position)
      logger.info("[Hettich %s] moving to rotor position %d (%s)", self.io.port, position, speed)
      await self._select_parameter(POSITION_COMMAND, 0x0001 if speed == "slow" else 0x0002)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
      state = await self.request_hatch_status()
      if state.positioning_timeout or state.positioning_error:
        raise HettichCentrifugeError(f"Positioning rotor at position {position} failed")
      if state.position_reached:
        return
      if loop.time() >= deadline:
        raise TimeoutError(f"Rotor did not reach position {position} within {timeout} seconds")
      await asyncio.sleep(self.poll_interval)

  async def _wait_for_positioning_end(self, timeout: float) -> None:
    """Wait until the positioning-active state clears."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
      hatch = await self.request_hatch_status()
      if hatch.positioning_timeout or hatch.positioning_error:
        raise HettichCentrifugeError("The centrifuge reported a positioning error")
      if not hatch.positioning_active:
        return
      if loop.time() >= deadline:
        raise TimeoutError(f"Positioning mode did not end within {timeout} seconds")
      await asyncio.sleep(self.poll_interval)

  async def end_positioning(self, timeout: float = 10.0) -> None:
    """Leave positioning mode if it is active, readying the centrifuge for a run."""
    if not (await self.request_hatch_status()).positioning_active:
      return
    self._require_positioning_ready(await self.request_status())
    await self._select_parameter(POSITION_COMMAND, 0x0080)
    await self._wait_for_positioning_end(timeout)

  async def recall_program(self, program: int) -> None:
    """Recall and activate a stored program (1..89)."""
    if not 1 <= program <= 89:
      raise ValueError("program must be 1..89")
    status = await self.request_status()
    self._require_remote_standstill(status)
    if status.program_number == program:
      return
    await self._select_parameter(PROGRAM_COMMAND, (program << 8) | 0x04)
    logger.info("[Hettich %s] activated program %d", self.io.port, program)

  async def _start_spin(self, run_time: int, speed: int) -> None:
    """Start without waiting, using the device-native acceleration-inclusive run time."""
    if not 1 <= run_time <= MAXIMUM_DURATION:
      raise ValueError(f"run_time must be 1..{MAXIMUM_DURATION} seconds")
    if speed < MINIMUM_SPEED:
      raise ValueError(f"speed must be at least {MINIMUM_SPEED} rpm")

    status = await self.request_status()
    self._require_remote_standstill(status)
    if not status.lid_closed:
      raise HettichCentrifugeError("Close and lock the main centrifuge lid before a run")
    if status.no_rotor:
      raise HettichCentrifugeError("The centrifuge does not detect an installed rotor")
    if status.maximum_rotor_cycles_exceeded:
      raise HettichCentrifugeError("The installed rotor's maximum cycle count is exceeded")
    hatch = await self.request_hatch_status()
    if not hatch.hatch_closed or not hatch.lid_lock_closed:
      raise HettichCentrifugeError("The hatch and lid lock must both be closed before a run")
    if hatch.positioning_active:
      await self._select_parameter(POSITION_COMMAND, 0x0080)
      await self._wait_for_positioning_end(timeout=10.0)
      status = await self.request_status()
      self._require_remote_standstill(status)
    if not status.can_start:
      raise HettichCentrifugeError("The centrifuge reports that centrifugation is not possible")

    maximum_speed = await self.request_maximum_speed()
    if speed > maximum_speed:
      raise ValueError(f"speed must not exceed the installed rotor limit of {maximum_speed} rpm")

    await self._select_parameter(RUN_TIME_PARAMETER, run_time)
    await self._select_parameter(SPEED_PARAMETER, speed)
    await self._select_parameter(ACTIVATE_PARAMETERS_COMMAND, 0x0001)

    logger.info(
      "[Hettich %s] starting centrifugation: run_time=%d seconds, speed=%d rpm",
      self.io.port,
      run_time,
      speed,
    )
    await self._select_parameter(SPIN_COMMAND, 0x0002)

  async def _wait_for_standstill(self, timeout: float, motion_observed: bool) -> CentrifugeStatus:
    """Wait for standstill, optionally accepting that motion was observed by the caller."""
    if timeout <= 0:
      raise ValueError("timeout must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
      status = await self.request_status()
      if status.error_number is not None:
        raise HettichCentrifugeError(f"The centrifuge stopped with error {status.error_number}")
      if status.phase != "standstill":
        motion_observed = True
      elif motion_observed:
        return status
      if loop.time() >= deadline:
        raise TimeoutError(f"Centrifuge did not return to standstill within {timeout} seconds")
      await asyncio.sleep(self.poll_interval)

  async def _wait_for_target_speed(self, speed: int, timeout: float) -> int:
    """Wait for ``speed`` and return the device's elapsed run time at that point."""
    if timeout <= 0:
      raise ValueError("timeout must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    motion_observed = False
    while True:
      status = await self.request_status()
      if status.error_number is not None:
        raise HettichCentrifugeError(f"The centrifuge stopped with error {status.error_number}")
      actual_speed = await self.request_speed()
      if status.phase != "standstill" or actual_speed > 0:
        motion_observed = True
      if status.phase == "centrifuging" and actual_speed >= speed:
        return await self.request_elapsed_time()
      if motion_observed and status.phase in ("braking", "standstill"):
        raise HettichCentrifugeError(
          f"The centrifuge began {status.phase} at {actual_speed} rpm before reaching "
          f"the target speed of {speed} rpm"
        )
      if loop.time() >= deadline:
        raise TimeoutError(f"Centrifuge did not reach {speed} rpm within {timeout} seconds")
      await asyncio.sleep(self.poll_interval)

  async def spin(
    self,
    duration: int,
    speed: int,
    timeout: Optional[float] = None,
  ) -> None:
    """Run a finite centrifugation cycle and block until standstill.

    Args:
      duration: Time in seconds at the target speed, excluding acceleration and braking.
      speed: Rotor speed in rpm, bounded at runtime by the installed rotor.
      timeout: Total wait timeout. By default this uses the centrifuge's maximum
        configured run-up and run-down times plus a communication margin.

    The centrifuge's native timer starts during acceleration. This method first
    gives that timer a bounded safety value, then replaces its normal end time
    once the measured rotor speed reaches ``speed``. Timing has one-second
    resolution, matching the device protocol.
    """
    if not 1 <= duration <= MAXIMUM_DURATION:
      raise ValueError(f"duration must be 1..{MAXIMUM_DURATION} seconds")
    if timeout is not None and timeout <= duration:
      raise ValueError("timeout must exceed duration to allow for acceleration and braking")

    maximum_run_up_time = await self._enquire_parameter(MAXIMUM_RUN_UP_TIME_PARAMETER)
    maximum_target_duration = MAXIMUM_DURATION - maximum_run_up_time
    if duration > maximum_target_duration:
      raise ValueError(
        f"duration must not exceed {maximum_target_duration} seconds with the centrifuge's "
        f"configured maximum run-up time of {maximum_run_up_time} seconds"
      )
    cycle_timeout: float
    if timeout is None:
      maximum_run_down_time = await self._enquire_parameter(MAXIMUM_RUN_DOWN_TIME_PARAMETER)
      cycle_timeout = (
        duration + maximum_run_up_time + maximum_run_down_time + DEFAULT_TIMEOUT_MARGIN
      )
    else:
      cycle_timeout = timeout

    loop = asyncio.get_running_loop()
    deadline = loop.time() + cycle_timeout
    initial_run_time = duration + maximum_run_up_time
    await self._start_spin(run_time=initial_run_time, speed=speed)
    try:
      remaining = deadline - loop.time()
      if remaining <= 0:
        raise TimeoutError(
          f"Centrifuge did not reach {speed} rpm within the {cycle_timeout}-second timeout"
        )
      elapsed_at_target = await self._wait_for_target_speed(speed=speed, timeout=remaining)
      end_time = elapsed_at_target + duration
      if end_time > MAXIMUM_DURATION:
        raise ValueError(
          "duration is too long to exclude acceleration within the device's maximum run time"
        )
      await self._select_parameter(RUN_TIME_PARAMETER, end_time)
      await self._select_parameter(ACTIVATE_PARAMETERS_COMMAND, 0x0001)

      remaining = deadline - loop.time()
      if remaining <= 0:
        raise TimeoutError(f"Centrifuge cycle exceeded its {cycle_timeout}-second timeout")
      await self._wait_for_standstill(timeout=remaining, motion_observed=True)
    except BaseException:
      try:
        await self.stop_spin()
      except BaseException:
        logger.exception("[Hettich %s] failed to stop after spin() failed", self.io.port)
      raise

  async def stop_spin(self, timeout: float = 300.0) -> None:
    """Emergency-stop an active run and wait for standstill.

    The Hettich manual explicitly classifies a PC STOP command as an emergency
    stop. If the rotor is already at standstill, this method is a no-op.
    """
    status = await self.request_status()
    if status.phase == "standstill":
      return
    logger.warning("[Hettich %s] emergency-stopping centrifugation", self.io.port)
    await self._select_parameter(SPIN_COMMAND, 0x0001)
    await self._wait_for_standstill(timeout=timeout, motion_observed=True)


class HettichCooledRoboticCentrifuge(HettichRoboticCentrifuge, ABC):
  """Abstract Generation 2 base for refrigerated Hettich robotic centrifuges."""

  async def request_temperature(self) -> float:
    """Return the actual chamber temperature in degrees Celsius."""
    raw = await self._enquire_parameter(TEMPERATURE_PARAMETER)
    return raw / 2 - 25


class HettichMikro220RoboticCentrifuge(HettichRoboticCentrifuge):
  """Hettich MIKRO 220 Robotic centrifuge."""

  @classmethod
  def _configuration(cls) -> _ModelConfiguration:
    """Return the MIKRO 220 POS protocol identity and rotor catalog."""
    return _MIKRO_220_ROBOTIC_CONFIGURATION


class HettichRotanta460RoboticCentrifuge(HettichCooledRoboticCentrifuge):
  """Hettich ROTANTA 460 Robotic refrigerated centrifuge."""

  @classmethod
  def _configuration(cls) -> _ModelConfiguration:
    """Return the ROTANTA 460 R POS protocol identity."""
    return _ROTANTA_460_ROBOTIC_CONFIGURATION


class HettichRotina380RoboticCentrifuge(HettichRoboticCentrifuge):
  """Hettich ROTINA 380 Robotic centrifuge."""

  @classmethod
  def _configuration(cls) -> _ModelConfiguration:
    """Return the ROTINA 380 POS protocol identity."""
    return _ROTINA_380_ROBOTIC_CONFIGURATION


class HettichRotina380RRoboticCentrifuge(HettichCooledRoboticCentrifuge):
  """Hettich ROTINA 380 R Robotic refrigerated centrifuge."""

  @classmethod
  def _configuration(cls) -> _ModelConfiguration:
    """Return the ROTINA 380 R POS protocol identity."""
    return _ROTINA_380_R_ROBOTIC_CONFIGURATION
