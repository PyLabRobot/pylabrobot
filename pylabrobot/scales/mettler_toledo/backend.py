"""Mettler Toledo scale backend using the MT-SICS (Mettler Toledo Standard Interface Command Set) serial protocol."""

# similar library: https://github.com/janelia-pypi/mettler_toledo_device_python

import asyncio
import functools
import inspect
import logging
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Optional, Set, TypeVar, Union

from pylabrobot.io.serial import Serial
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.scales.mettler_toledo.confirmed_firmware_versions import CONFIRMED_FIRMWARE_VERSIONS
from pylabrobot.scales.mettler_toledo.errors import MettlerToledoError
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")


@dataclass
class MettlerToledoResponse:
  """A single parsed MT-SICS response line.

  Format: <command> <status> [<data> ...] CR LF
  See protocol.md for full format description.
  """

  command: str
  status: str
  data: List[str] = field(default_factory=list)


F = TypeVar("F", bound=Callable[..., Any])


def requires_mt_sics_command(mt_sics_command: str) -> Callable[[F], F]:
  """Decorator that gates a method on the connected device supporting a specific MT-SICS command.

  During setup(), the backend queries I0 to discover the full list of implemented commands.
  Methods decorated with a command not in that list will raise MettlerToledoError.

  I0 is the definitive source of command support - I1 only reports which standardized
  level sets are fully implemented, but individual commands may exist outside those levels.
  """

  def decorator(func: F) -> F:
    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
      if hasattr(self, "_supported_commands") and self._supported_commands is not None:
        if mt_sics_command not in self._supported_commands:
          raise MettlerToledoError(
            title="Command not supported",
            message=f"'{func.__name__}' requires MT-SICS command '{mt_sics_command}', "
            f"which is not implemented on this device.",
          )
      return await func(self, *args, **kwargs)

    # Register in the class-level command-to-method mapping.
    _MT_SICS_COMMAND_REGISTRY[func.__name__] = mt_sics_command
    return wrapper  # type: ignore[return-value]

  return decorator


# Maps method name -> MT-SICS command string, populated by @requires_mt_sics_command.
_MT_SICS_COMMAND_REGISTRY: dict[str, str] = {}


# TODO: rename to MTSICSDriver in v1.0.0-beta
class MettlerToledoWXS205SDUBackend(ScaleBackend):
  """Backend for Mettler Toledo scales using the MT-SICS protocol.

  MT-SICS (Mettler Toledo Standard Interface Command Set) is the serial communication
  protocol used by Mettler Toledo's Automated Precision Weigh Modules. This backend is
  compatible with any MT-SICS device, including the WXS, WMS, and WX series.

  During setup(), the backend queries I0 to discover which commands the connected device
  supports, then queries I1/I2/I4 for device identity. Methods decorated with
  ``@requires_mt_sics_command`` will raise ``MettlerToledoError`` if the required command
  is not in the device's I0 command list.

  Tested on the WXS205SDU (used by Hamilton in the Liquid Verification Kit).

  Spec: https://web.archive.org/web/20240208213802/https://www.mt.com/dam/
  product_organizations/industry/apw/generic/11781363_N_MAN_RM_MT-SICS_APW_en.pdf

  From the spec (Section 2.2):
    "If several commands are sent in succession without waiting for the corresponding
    responses, it is possible that the weigh module/balance confuses the sequence of
    command processing or ignores entire commands."
  """

  # === Constructor ===

  def __init__(self, port: Optional[str] = None, vid: int = 0x0403, pid: int = 0x6001):
    """Create a new MT-SICS backend.

    Args:
      port: Serial port path. If None, auto-detected by VID:PID.
      vid: USB vendor ID (default 0x0403 = FTDI).
      pid: USB product ID (default 0x6001 = FT232R).
    """
    super().__init__()

    self.io = Serial(
      human_readable_device_name="Mettler Toledo Scale",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=9600,
      timeout=1,
    )

  async def setup(self) -> None:
    """Connect to the scale, reset to clean state, discover identity and supported commands."""
    await self.io.setup()

    # Reset device to clean state (spec Section 2.2)
    # reset() clears the input buffer and sends @, which returns the serial number
    self.serial_number = await self.reset()

    # Discover supported commands via I0 (the definitive source per spec Section 2.2)
    self._supported_commands: Set[str] = await self._request_supported_commands()

    # Device identity (Level 0 - always available)
    # Note: device_type and capacity both use I2 but are separate methods intentionally -
    # single-responsibility per method, the duplicate I2 round-trip during one-time setup is fine.
    self.device_type = await self.request_device_type()
    self.capacity = await self.request_capacity()

    # Firmware version and configuration
    self.firmware_version = await self.request_firmware_version()
    # I2 device_type encodes the configuration: "WXS205SDU WXA-Bridge" = bridge only
    self.configuration = "Bridge" if "Bridge" in self.device_type else "Balance"

    logger.info(
      "[%s] Connected on %s\n"
      "Device type: %s\n"
      "Configuration: %s\n"
      "Serial number: %s\n"
      "Firmware: %s\n"
      "Capacity: %.1f g\n"
      "Supported commands (%d): %s",
      self.io._human_readable_device_name,
      self.io.port,
      self.device_type,
      self.configuration,
      self.serial_number,
      self.firmware_version,
      self.capacity,
      len(self._supported_commands),
      ", ".join(sorted(self._supported_commands)),
    )

    # Check major.minor version only (TDNR varies by hardware revision)
    fw_version_short = self.firmware_version.split()[0] if self.firmware_version else ""
    if fw_version_short not in CONFIRMED_FIRMWARE_VERSIONS:
      logger.warning(
        "[%s] Firmware version %r has not been tested with this driver. "
        "Confirmed versions: %s. "
        "If this version works correctly, please contribute it to "
        "confirmed_firmware_versions.py so others can benefit.",
        self.io._human_readable_device_name,
        self.firmware_version,
        ", ".join(sorted(CONFIRMED_FIRMWARE_VERSIONS)),
      )

    # Set output unit to grams
    if "M21" in self._supported_commands:
      await self.set_host_unit_grams()

  async def stop(self) -> None:
    """Reset the device to a clean state and close the serial connection.

    Sends @ to cancel any pending commands before disconnecting. If the
    serial port is already broken (e.g. kernel crash), the reset is skipped
    and the port is closed anyway.
    """
    try:
      await self.reset()
    except (OSError, TimeoutError, MettlerToledoError):
      logger.warning(
        "[%s] Could not reset device before disconnecting", self.io._human_readable_device_name
      )
    logger.info("[%s] Disconnected from %s", self.io._human_readable_device_name, self.io.port)
    await self.io.stop()

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.io.port}

  # === Device discovery ===

  async def _request_supported_commands(self) -> Set[str]:
    """Query all implemented MT-SICS commands via I0 (Level 0 - always available).

    I0 is the definitive source of command support per spec Section 2.2.
    I1 only reports which standardized level sets are fully implemented,
    but individual commands may exist outside those levels.

    Returns a set of MT-SICS command strings (e.g. {"@", "S", "SI", "Z", "M21", "M28"}).
    """
    responses = await self.send_command("I0")
    commands: Set[str] = set()
    for resp in responses:
      # Format: I0 B/A <level> <command>
      if len(resp.data) >= 2:
        commands.add(resp.data[1])
    return commands

  def request_supported_methods(self) -> List[str]:
    """Return the names of all backend methods supported by the connected device.

    Uses the I0 command list (populated during setup) to determine which
    decorated methods can be called without raising MettlerToledoError.
    Undecorated methods (Level 0/1) are always included.
    """
    supported: List[str] = []
    for name in sorted(inspect.getmembers(self, predicate=inspect.ismethod), key=lambda m: m[0]):
      method_name = name[0]
      if method_name.startswith("_"):
        continue
      if method_name in _MT_SICS_COMMAND_REGISTRY:
        mt_cmd = _MT_SICS_COMMAND_REGISTRY[method_name]
        if hasattr(self, "_supported_commands") and mt_cmd in self._supported_commands:
          supported.append(method_name)
      else:
        supported.append(method_name)
    return supported

  # === Response parsing ===

  @staticmethod
  def _validate_response(response: MettlerToledoResponse, min_fields: int, command: str) -> None:
    """Validate that a parsed response has the expected minimum total field count.

    min_fields counts all fields (command + status + data). For example,
    a weight response "S S 0.00006 g" has 4 fields total.

    Raises:
      MettlerToledoError: if the response has fewer fields than expected.
    """
    total = 1 + (1 if response.status else 0) + len(response.data)
    if total < min_fields:
      raise MettlerToledoError(
        title="Unexpected response",
        message=f"Expected at least {min_fields} fields for '{command}', got {total}: {response}",
      )

  @staticmethod
  def _validate_unit(unit: str, command: str) -> None:
    """Validate that the unit in a response is grams.

    Raises:
      MettlerToledoError: if the unit is not 'g'.
    """
    if unit != "g":
      raise MettlerToledoError(
        title="Unexpected unit",
        message=f"Expected 'g' for '{command}', got '{unit}'",
      )

  def _parse_basic_errors(self, response: MettlerToledoResponse) -> None:
    """Helper function for parsing basic errors that are common to many commands. If an error is
    detected, a 'MettlerToledoError' exception is raised.

    Error commands (ES, ET, EL) have status="" and no data.
    Status codes I, L, +, - indicate command-specific errors.

    Note: B status (multi-response) is handled by send_command, which reads all lines
    until status A. Each line is validated through this method individually.
    """

    # General error messages: ES, ET, EL (status is "" for these)
    if response.command == "ES":
      raise MettlerToledoError.syntax_error()
    if response.command == "ET":
      raise MettlerToledoError.transmission_error()
    if response.command == "EL":
      raise MettlerToledoError.logical_error()

    # Status code errors
    if response.status == "I":
      raise MettlerToledoError.executing_another_command()
    if response.status == "L":
      raise MettlerToledoError.incorrect_parameter()
    if response.status == "+":
      raise MettlerToledoError.overload()
    if response.status == "-":
      raise MettlerToledoError.underload()

    # Weight response error: S S Error <code><trigger>
    if (
      response.command == "S"
      and response.status == "S"
      and len(response.data) >= 2
      and response.data[0] == "Error"
    ):
      error_code = response.data[1]
      code, source = error_code[:-1], error_code[-1]
      from_terminal = source == "t"
      if code == "1":
        raise MettlerToledoError.boot_error(from_terminal=from_terminal)
      if code == "2":
        raise MettlerToledoError.brand_error(from_terminal=from_terminal)
      if code == "3":
        raise MettlerToledoError.checksum_error(from_terminal=from_terminal)
      if code == "9":
        raise MettlerToledoError.option_fail(from_terminal=from_terminal)
      if code == "10":
        raise MettlerToledoError.eeprom_error(from_terminal=from_terminal)
      if code == "11":
        raise MettlerToledoError.device_mismatch(from_terminal=from_terminal)
      if code == "12":
        raise MettlerToledoError.hot_plug_out(from_terminal=from_terminal)
      if code == "14":
        raise MettlerToledoError.weight_module_electronic_mismatch(from_terminal=from_terminal)
      if code == "15":
        raise MettlerToledoError.adjustment_needed(from_terminal=from_terminal)
      raise MettlerToledoError(
        title="Unknown weight error",
        message=f"Unrecognized error code '{error_code}' in weight response",
      )

  # === Command Layer ===

  async def send_command(self, command: str, timeout: int = 60) -> List[MettlerToledoResponse]:
    """Send a command to the scale and read all response lines.

    Single-response commands (status A) return a list of one parsed line.
    Multi-response commands (status B) return all lines, reading until status A.

    Args:
      timeout: The timeout in seconds (applies across all response lines).
    """

    logger.log(LOG_LEVEL_IO, "[%s] Sent command: %s", self.io._human_readable_device_name, command)
    await self.io.write(command.encode() + b"\r\n")

    try:
      responses: List[MettlerToledoResponse] = []
      timeout_time = time.time() + timeout
      while True:
        while True:
          raw_response = await self.io.readline()
          if raw_response != b"":
            break
          if time.time() > timeout_time:
            raise TimeoutError("Timeout while waiting for response from scale.")
          await asyncio.sleep(0.001)

        logger.log(
          LOG_LEVEL_IO,
          "[%s] Received response: %s",
          self.io._human_readable_device_name,
          raw_response,
        )
        fields = shlex.split(raw_response.decode("utf-8").strip())
        if len(fields) >= 2:
          response = MettlerToledoResponse(command=fields[0], status=fields[1], data=fields[2:])
        elif len(fields) == 1:
          response = MettlerToledoResponse(command=fields[0], status="", data=[])
        else:
          response = MettlerToledoResponse(command="", status="", data=[])
        self._parse_basic_errors(response)
        responses.append(response)

        # Status B means more responses follow; anything else (A, etc.) is final
        if response.status != "B":
          break

      return responses

    except (KeyboardInterrupt, asyncio.CancelledError):
      # Cancel pending commands without resetting device state (zero/tare).
      # Use C (cancel all) if available; otherwise just flush the buffer.
      # Never send @ here - it clears zero/tare which the user wants to keep.
      if hasattr(self, "_supported_commands") and "C" in self._supported_commands:
        logger.warning(
          "[%s] Command interrupted, sending C to cancel pending commands",
          self.io._human_readable_device_name,
        )
        await self.io.write(b"C\r\n")
      else:
        logger.warning(
          "[%s] Command interrupted, flushing serial buffer",
          self.io._human_readable_device_name,
        )
      await self.io.reset_input_buffer()
      raise

  # === Public API ===
  # Organized by function: cancel, identity, zero, tare, weight, measurement,
  # configuration (read), display, configuration (write).

  # # Reset and cancel # #

  async def reset(self) -> str:
    """@ - Reset the device to a determined state (spec Section 2.2).

    Equivalent to a power cycle: empties volatile memories, resets key control
    to default. Tare memory is NOT reset. Always executed, even when busy.

    Returns the serial number from the I4-style response.
    """
    await self.io.reset_input_buffer()
    responses = await self.send_command("@")
    # @ responds with I4-style: I4 A "<SNR>"
    self._validate_response(responses[0], 3, "@")
    return responses[0].data[0]

  @requires_mt_sics_command("C")
  async def cancel_all(self) -> None:
    """C - Cancel all active and pending interface commands.

    Unlike reset() (@), this does not reset the device - it only cancels
    commands that were requested via this interface. Typically used to stop
    repeating commands (SIR, SR) or abort adjustment procedures.

    This is a multi-response command: the device sends C B (started) then
    C A (complete). Both responses are consumed to keep the serial buffer clean.
    """
    responses = await self.send_command("C")
    # send_command reads both C B (started) and C A (complete) automatically
    self._validate_response(responses[0], 2, "C")
    if responses[0].status == "E":
      raise MettlerToledoError(
        title="Error while canceling",
        message=f"C command returned error: {responses[0]}",
      )

  # # Device identity # #

  async def request_serial_number(self) -> str:
    """Get the serial number of the scale. (I4 command)"""
    responses = await self.send_command("I4")
    self._validate_response(responses[0], 3, "I4")
    return responses[0].data[0]

  async def request_device_type(self) -> str:
    """Query the device type string. (I2 command)

    The I2 response packs type, capacity, and unit into a single quoted string:
    I2 A "WXS205SDU WXA-Bridge 220.00900 g"
    The type is everything before the last two tokens (capacity and unit).
    """
    responses = await self.send_command("I2")
    self._validate_response(responses[0], 3, "I2")
    parts = responses[0].data[0].split()
    return " ".join(parts[:-2])

  async def request_capacity(self) -> float:
    """Query the maximum weighing capacity in grams. (I2 command)

    The I2 response packs type, capacity, and unit into a single quoted string:
    I2 A "WXS205SDU WXA-Bridge 220.00900 g"
    Capacity is the second-to-last token, unit is the last.
    """
    responses = await self.send_command("I2")
    self._validate_response(responses[0], 3, "I2")
    parts = responses[0].data[0].split()
    self._validate_unit(parts[-1], "I2")
    return float(parts[-2])

  async def request_firmware_version(self) -> str:
    """Query the firmware version and type definition number. (I3 command)

    Returns the version string (e.g. "1.10 18.6.4.1361.772").
    For bridge mode (no terminal), returns the bridge firmware version.
    """
    responses = await self.send_command("I3")
    self._validate_response(responses[0], 3, "I3")
    return responses[0].data[0]

  async def request_software_material_number(self) -> str:
    """Query the software material number (SW-ID). (I5 command)

    Unique per software release: 8-digit number + alphabetic index.
    For bridge mode (no terminal), returns the bridge SW-ID.
    """
    responses = await self.send_command("I5")
    self._validate_response(responses[0], 3, "I5")
    return responses[0].data[0]

  @requires_mt_sics_command("I10")
  async def request_device_id(self) -> str:
    """Query the user-assigned device identification string. (I10 command)

    This is a user-configurable name (max 20 chars) to identify
    individual scales in multi-scale setups. Retained after @ cancel.
    """
    responses = await self.send_command("I10")
    self._validate_response(responses[0], 3, "I10")
    return responses[0].data[0]

  @requires_mt_sics_command("I10")
  async def set_device_id(self, device_id: str) -> None:
    """Set the user-assigned device identification string. (I10 command)

    Max 20 alphanumeric characters. Persists across power cycles.
    Useful for labeling individual scales in multi-scale setups.
    """
    await self.send_command(f'I10 "{device_id}"')

  @requires_mt_sics_command("I11")
  async def request_model_designation(self) -> str:
    """Query the model designation string. (I11 command)

    Returns the weigh module model type (e.g. "WMS404C-L/10").
    Abbreviations: DR=Delta Range, DU=Dual Range, /M or /A=Approved.
    """
    responses = await self.send_command("I11")
    self._validate_response(responses[0], 3, "I11")
    return responses[0].data[0]

  @requires_mt_sics_command("I14")
  async def request_device_info(self, category: int = 0) -> List[MettlerToledoResponse]:
    """Query detailed device information for a specific category. (I14 command)

    Args:
      category: Information category to query:
        0 = instrument configuration (Bridge, Terminal, Option)
        1 = instrument descriptions (model names)
        2 = SW identification numbers
        3 = SW versions
        4 = serial numbers
        5 = TDNR (type definition) numbers

    Returns multi-response with data for each component (bridge, terminal, etc.).
    """
    return await self.send_command(f"I14 {category}")

  @requires_mt_sics_command("I15")
  async def request_uptime_minutes(self) -> int:
    """Query the uptime in minutes since last start or restart. (I15 command)

    Returns the number of minutes the device has been running since
    the last power-on, start, or reset. Accuracy +/- 5%.
    """
    responses = await self.send_command("I15")
    self._validate_response(responses[0], 3, "I15")
    return int(responses[0].data[0])

  @requires_mt_sics_command("DAT")
  async def request_date(self) -> str:
    """Query the current date from the device. (DAT command)

    Response format: DAT A <Day> <Month> <Year>.
    Returns the date as "DD.MM.YYYY".
    """
    responses = await self.send_command("DAT")
    self._validate_response(responses[0], 5, "DAT")
    day, month, year = responses[0].data[0], responses[0].data[1], responses[0].data[2]
    return f"{day}.{month}.{year}"

  @requires_mt_sics_command("DAT")
  async def set_date(self, day: int, month: int, year: int) -> None:
    """Set the device date. (DAT command)

    Args:
      day: Day (1-31).
      month: Month (1-12).
      year: Year (2020-2099, platform-dependent).
    """
    await self.send_command(f"DAT {day:02d} {month:02d} {year}")

  @requires_mt_sics_command("TIM")
  async def request_time(self) -> str:
    """Query the current time from the device. (TIM command)

    Response format: TIM A <Hour> <Minute> <Second>.
    Returns the time as "HH:MM:SS".
    """
    responses = await self.send_command("TIM")
    self._validate_response(responses[0], 5, "TIM")
    hour, minute, second = responses[0].data[0], responses[0].data[1], responses[0].data[2]
    return f"{hour}:{minute}:{second}"

  @requires_mt_sics_command("TIM")
  async def set_time(self, hour: int, minute: int, second: int) -> None:
    """Set the device time. (TIM command)

    Persists across power cycles. Only reset via FSET or terminal menu, not @.

    Args:
      hour: Hour (0-23).
      minute: Minute (0-59).
      second: Second (0-59).
    """
    await self.send_command(f"TIM {hour:02d} {minute:02d} {second:02d}")

  @requires_mt_sics_command("I16")
  async def request_next_service_date(self) -> str:
    """Query the date when the balance is next due to be serviced. (I16 command)

    Returns the date as "DD.MM.YYYY".
    """
    responses = await self.send_command("I16")
    self._validate_response(responses[0], 5, "I16")
    day, month, year = responses[0].data[0], responses[0].data[1], responses[0].data[2]
    return f"{day}.{month}.{year}"

  @requires_mt_sics_command("I21")
  async def request_assortment_type_revision(self) -> str:
    """Query the revision of assortment type tolerances. (I21 command)"""
    responses = await self.send_command("I21")
    self._validate_response(responses[0], 3, "I21")
    return responses[0].data[0]

  @requires_mt_sics_command("I26")
  async def request_operating_mode_after_restart(self) -> List[MettlerToledoResponse]:
    """Query the operating mode after restart. (I26 command)"""
    return await self.send_command("I26")

  # # Zero # #

  async def zero_immediately(self) -> List[MettlerToledoResponse]:
    """Zero the scale immediately. (ZI command)"""
    return await self.send_command("ZI")

  async def zero_stable(self) -> List[MettlerToledoResponse]:
    """Zero the scale when the weight is stable. (Z command)"""
    return await self.send_command("Z")

  @requires_mt_sics_command("ZC")
  async def zero_timeout(self, timeout: float) -> List[MettlerToledoResponse]:
    """Zero the scale after a given timeout. (ZC command)"""
    timeout_ms = int(timeout * 1000)
    return await self.send_command(f"ZC {timeout_ms}")

  async def zero(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> List[MettlerToledoResponse]:
    """Zero the scale.

    Args:
      timeout: "stable" waits for stable reading, 0 zeros immediately,
        float/int zeros after that many seconds.
    """
    if timeout == "stable":
      return await self.zero_stable()
    if not isinstance(timeout, (float, int)):
      raise TypeError("timeout must be a float or 'stable'")
    if timeout < 0:
      raise ValueError("timeout must be greater than or equal to 0")
    if timeout == 0:
      return await self.zero_immediately()
    return await self.zero_timeout(timeout)

  # # Tare # #

  async def tare_stable(self) -> List[MettlerToledoResponse]:
    """Tare the scale when the weight is stable. (T command)"""
    return await self.send_command("T")

  async def tare_immediately(self) -> List[MettlerToledoResponse]:
    """Tare the scale immediately. (TI command)"""
    return await self.send_command("TI")

  @requires_mt_sics_command("TC")
  async def tare_timeout(self, timeout: float) -> List[MettlerToledoResponse]:
    """Tare the scale after a given timeout. (TC command)"""
    timeout_ms = int(timeout * 1000)
    return await self.send_command(f"TC {timeout_ms}")

  async def tare(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> List[MettlerToledoResponse]:
    """Tare the scale.

    Args:
      timeout: "stable" waits for stable reading, 0 tares immediately,
        float/int tares after that many seconds.
    """
    if timeout == "stable":
      return await self.tare_stable()
    if not isinstance(timeout, (float, int)):
      raise TypeError("timeout must be a float or 'stable'")
    if timeout < 0:
      raise ValueError("timeout must be greater than or equal to 0")
    if timeout == 0:
      return await self.tare_immediately()
    return await self.tare_timeout(timeout)

  async def request_tare_weight(self) -> float:
    """Query tare weight value from scale's memory. (TA command)"""
    responses = await self.send_command("TA")
    self._validate_response(responses[0], 4, "TA")
    self._validate_unit(responses[0].data[1], "TA")
    return float(responses[0].data[0])

  async def clear_tare(self) -> List[MettlerToledoResponse]:
    """Clear tare weight value. (TAC command)"""
    return await self.send_command("TAC")

  # # Weight measurement # #

  async def read_stable_weight(self) -> float:
    """Read a stable weight value from the scale. (MEASUREMENT command)

    from the docs:

    "Use S to send a stable weight value, along with the host unit, from the balance to
    the connected communication partner via the interface. If the automatic door function
    is enabled and a stable weight is requested the balance will open and close the balance's
    doors to achieve a stable weight."
    """

    responses = await self.send_command("S")
    self._validate_response(responses[0], 4, "S")
    self._validate_unit(responses[0].data[1], "S")
    return float(responses[0].data[0])

  @requires_mt_sics_command("SC")
  async def read_dynamic_weight(self, timeout: float) -> float:
    """Read a stable weight value within a given timeout, or return the current
    weight value if stability is not reached. (SC command)

    Args:
      timeout: The timeout in seconds.
    """
    timeout_ms = int(timeout * 1000)
    responses = await self.send_command(f"SC {timeout_ms}")
    self._validate_response(responses[0], 4, "SC")
    self._validate_unit(responses[0].data[1], "SC")
    return float(responses[0].data[0])

  async def read_weight_value_immediately(self) -> float:
    """Read a weight value immediately from the scale. (SI command)"""
    responses = await self.send_command("SI")
    self._validate_response(responses[0], 4, "SI")
    self._validate_unit(responses[0].data[1], "SI")
    return float(responses[0].data[0])

  async def read_weight(self, timeout: Union[Literal["stable"], float, int] = "stable") -> float:
    """High level function to read a weight value from the scale. (MEASUREMENT command)

    Args:
      timeout: The timeout in seconds. If "stable", the scale will return a weight value when the
        weight is stable. If 0, the scale will return a weight value immediately. If a float/int,
        the scale will return a weight value after the given timeout (in seconds).
    """

    if timeout == "stable":
      return await self.read_stable_weight()

    if not isinstance(timeout, (float, int)):
      raise TypeError("timeout must be a float or 'stable'")

    if timeout < 0:
      raise ValueError("timeout must be greater than or equal to 0")

    if timeout == 0:
      return await self.read_weight_value_immediately()

    return await self.read_dynamic_weight(timeout)

  @requires_mt_sics_command("M28")
  async def measure_temperature(self) -> float:
    """Read the current temperature from the scale's internal sensor in degrees C. (M28 command)

    The number of temperature sensors depends on the product. This method returns
    the value from the first sensor. Useful for gravimetric verification where
    temperature affects liquid density and evaporation rate.
    """
    responses = await self.send_command("M28")
    self._validate_response(responses[0], 4, "M28")
    return float(responses[0].data[1])

  @requires_mt_sics_command("SIS")
  async def request_net_weight_with_status(self) -> MettlerToledoResponse:
    """Query net weight with unit and weighing status in one call. (SIS command)

    Response data fields:
      data[0] = State: 0=stable, 1=dynamic, 2=stable inaccurate (MinWeigh),
                3=dynamic inaccurate, 4=overload, 5=underload, 6=error
      data[1] = Net weight value
      data[2] = Unit code: 0=g, 1=kg, 3=mg, 4=ug, 5=ct, 7=lb, 8=oz, etc.
      data[3] = Readability (number of decimal places, 0-6)
      data[4] = Step: 1, 2, 5, 10, 20, 50, or 100
      data[5] = Approval: 0=standard (not approved), 1=e=d, 10=e=10d, 100=e=100d, -1=unapproved
      data[6] = Info: 0=without tare, 1=net with weighed tare, 2=net with stored tare
    """
    responses = await self.send_command("SIS")
    return responses[0]

  @requires_mt_sics_command("SNR")
  async def read_stable_weight_repeat_on_change(self) -> List[MettlerToledoResponse]:
    """Start sending stable weight values on every stable weight change. (SNR command)

    The device sends a new value each time the weight changes and stabilizes.
    Use reset() to stop.
    """
    return await self.send_command("SNR")

  # # Device configuration (read-only) # #

  @requires_mt_sics_command("M01")
  async def request_weighing_mode(self) -> int:
    """Query the current weighing mode. (M01 command)

    Returns: 0=Normal/Universal, 1=Dosing, 2=Sensor, 3=Check weighing, 6=Raw/No filter.
    """
    responses = await self.send_command("M01")
    self._validate_response(responses[0], 3, "M01")
    return int(responses[0].data[0])

  # @requires_mt_sics_command("M01")
  # async def set_weighing_mode(self, mode: int) -> None:
  #   """Set weighing mode. (M01) WRITES TO DEVICE MEMORY."""
  #   await self.send_command(f"M01 {mode}")

  @requires_mt_sics_command("M02")
  async def request_environment_condition(self) -> int:
    """Query the current environment condition setting. (M02 command)

    Returns: 0=Very stable, 1=Stable, 2=Standard, 3=Unstable, 4=Very unstable, 5=Automatic.
    Affects the scale's internal filter and stability detection.
    """
    responses = await self.send_command("M02")
    self._validate_response(responses[0], 3, "M02")
    return int(responses[0].data[0])

  # @requires_mt_sics_command("M02")
  # async def set_environment_condition(self, condition: int) -> None:
  #   """Set environment condition. (M02) WRITES TO DEVICE MEMORY."""
  #   await self.send_command(f"M02 {condition}")

  @requires_mt_sics_command("M03")
  async def request_auto_zero(self) -> int:
    """Query the current auto zero setting. (M03 command)

    Returns: 0=off, 1=on. Auto zero compensates for slow drift
    (e.g. evaporation, temperature changes) by automatically
    re-zeroing when the weight is near zero and stable.
    """
    responses = await self.send_command("M03")
    self._validate_response(responses[0], 3, "M03")
    return int(responses[0].data[0])

  # @requires_mt_sics_command("M03")
  # async def set_auto_zero(self, enabled: int) -> None:
  #   """Set auto zero. (M03) WRITES TO DEVICE MEMORY."""
  #   await self.send_command(f"M03 {enabled}")

  @requires_mt_sics_command("M17")
  async def request_profact_time_criteria(self) -> List[MettlerToledoResponse]:
    """Query ProFACT single time criteria. (M17 command)"""
    return await self.send_command("M17")

  # @requires_mt_sics_command("M17")
  # async def set_profact_time_criteria(self, ...) -> None:
  #   """Set ProFACT time criteria. (M17) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M18")
  async def request_profact_temperature_criterion(self) -> List[MettlerToledoResponse]:
    """Query ProFACT/FACT temperature criterion. (M18 command)"""
    return await self.send_command("M18")

  # @requires_mt_sics_command("M18")
  # async def set_profact_temperature_criterion(self, ...) -> None:
  #   """Set ProFACT temperature criterion. (M18) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M19")
  async def request_adjustment_weight(self) -> List[MettlerToledoResponse]:
    """Query the adjustment weight setting. (M19 command)"""
    return await self.send_command("M19")

  # @requires_mt_sics_command("M19")
  # async def set_adjustment_weight(self, ...) -> None:
  #   """Set adjustment weight. (M19) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M20")
  async def request_test_weight(self) -> List[MettlerToledoResponse]:
    """Query the test weight setting. (M20 command)"""
    return await self.send_command("M20")

  # @requires_mt_sics_command("M20")
  # async def set_test_weight(self, ...) -> None:
  #   """Set test weight. (M20) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M29")
  async def request_weighing_value_release(self) -> List[MettlerToledoResponse]:
    """Query the weighing value release setting. (M29 command)"""
    return await self.send_command("M29")

  # @requires_mt_sics_command("M29")
  # async def set_weighing_value_release(self, ...) -> None:
  #   """Set weighing value release. (M29) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M31")
  async def request_operating_mode(self) -> List[MettlerToledoResponse]:
    """Query the operating mode after restart. (M31 command)"""
    return await self.send_command("M31")

  # @requires_mt_sics_command("M31")
  # async def set_operating_mode(self, ...) -> None:
  #   """Set operating mode after restart. (M31) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M32")
  async def request_profact_time(self) -> List[MettlerToledoResponse]:
    """Query ProFACT time criteria. (M32 command)"""
    return await self.send_command("M32")

  # @requires_mt_sics_command("M32")
  # async def set_profact_time(self, ...) -> None:
  #   """Set ProFACT time. (M32) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M33")
  async def request_profact_day(self) -> List[MettlerToledoResponse]:
    """Query ProFACT day of the week. (M33 command)"""
    return await self.send_command("M33")

  # @requires_mt_sics_command("M33")
  # async def set_profact_day(self, ...) -> None:
  #   """Set ProFACT day of the week. (M33) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("M35")
  async def request_zeroing_mode(self) -> List[MettlerToledoResponse]:
    """Query the zeroing mode at startup. (M35 command)"""
    return await self.send_command("M35")

  # @requires_mt_sics_command("M35")
  # async def set_zeroing_mode(self, ...) -> None:
  #   """Set zeroing mode at startup. (M35) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("UPD")
  async def request_update_rate(self) -> float:
    """Query the current update rate for SIR/SIRU streaming. (UPD command)

    Returns the update rate in values per second.
    """
    responses = await self.send_command("UPD")
    self._validate_response(responses[0], 3, "UPD")
    return float(responses[0].data[0])

  # @requires_mt_sics_command("UPD")
  # async def set_update_rate(self, rate: float) -> None:
  #   """Set streaming update rate. (UPD) WRITES TO DEVICE MEMORY."""
  #   await self.send_command(f"UPD {rate}")

  @requires_mt_sics_command("C0")
  async def request_adjustment_setting(self) -> List[MettlerToledoResponse]:
    """Query the current adjustment setting. (C0 command)"""
    return await self.send_command("C0")

  # @requires_mt_sics_command("C0")
  # async def set_adjustment_setting(self, ...) -> None:
  #   """Set adjustment setting. (C0) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("COM")
  async def request_serial_parameters(self) -> List[MettlerToledoResponse]:
    """Query current serial interface parameters. (COM command)"""
    return await self.send_command("COM")

  # @requires_mt_sics_command("COM")
  # async def set_serial_parameters(self, ...) -> None:
  #   """Set serial port parameters. (COM) WRITES TO DEVICE MEMORY.
  #   WARNING: changing baud rate will lose communication."""
  #   ...

  @requires_mt_sics_command("FCUT")
  async def request_filter_cutoff(self) -> List[MettlerToledoResponse]:
    """Query the filter cut-off frequency. (FCUT command)"""
    return await self.send_command("FCUT")

  # @requires_mt_sics_command("FCUT")
  # async def set_filter_cutoff(self, frequency: float) -> None:
  #   """Set filter cut-off frequency. (FCUT) WRITES TO DEVICE MEMORY."""
  #   await self.send_command(f"FCUT {frequency}")

  @requires_mt_sics_command("USTB")
  async def request_stability_criteria(self) -> List[MettlerToledoResponse]:
    """Query the user-defined stability criteria. (USTB command)"""
    return await self.send_command("USTB")

  # @requires_mt_sics_command("USTB")
  # async def set_stability_criteria(self, ...) -> None:
  #   """Set stability criteria. (USTB) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("TST0")
  async def request_test_settings(self) -> List[MettlerToledoResponse]:
    """Query current test function settings. (TST0 command)"""
    return await self.send_command("TST0")

  # @requires_mt_sics_command("TST0")
  # async def set_test_settings(self, ...) -> None:
  #   """Set test function settings. (TST0) WRITES TO DEVICE MEMORY."""
  #   ...

  @requires_mt_sics_command("I50")
  async def request_remaining_weighing_range(self) -> float:
    """Query remaining maximum weighing range in grams. (I50 command)

    Returns the remaining capacity accounting for all loads currently on the
    weighing platform (pre-load, tare, net load). A negative value means the
    maximum weighing range has been exceeded.

    Multi-response: the device sends up to 3 lines (B, B, A).
    """
    responses = await self.send_command("I50")
    self._validate_response(responses[0], 5, "I50")
    self._validate_unit(responses[0].data[2], "I50")
    return float(responses[0].data[1])

  @requires_mt_sics_command("M27")
  async def request_adjustment_history(self) -> List[MettlerToledoResponse]:
    """Query the adjustment (calibration) history. (M27 command)

    Returns multi-response with each adjustment entry containing:
    entry number, date, time, mode (0=built-in, 1=external), and weight used.
    """
    return await self.send_command("M27")

  @requires_mt_sics_command("LST")
  async def request_user_settings(self) -> List[MettlerToledoResponse]:
    """Query all current user-configurable settings. (LST command)

    Returns a multi-response listing every configurable parameter and its value.
    """
    return await self.send_command("LST")

  @requires_mt_sics_command("RDB")
  async def request_readability(self) -> List[MettlerToledoResponse]:
    """Query the readability setting. (RDB command)"""
    return await self.send_command("RDB")

  # # Display # #

  @requires_mt_sics_command("D")
  async def set_display_text(self, text: str) -> List[MettlerToledoResponse]:
    """Write text to the display. (D command)

    Use set_weight_display() to restore the normal weight display.
    """
    return await self.send_command(f'D "{text}"')

  @requires_mt_sics_command("DW")
  async def set_weight_display(self) -> List[MettlerToledoResponse]:
    """Restore the normal weight display. (DW command)"""
    return await self.send_command("DW")

  # # Configuration (write - no corresponding query) # #

  @requires_mt_sics_command("M21")
  async def set_host_unit_grams(self) -> List[MettlerToledoResponse]:
    """Set the host output unit to grams. (M21 command)

    Called automatically during setup() if supported.
    """
    return await self.send_command("M21 0 0")

  # # Commented out - standalone write commands # #
  #
  # @requires_mt_sics_command("FSET")
  # async def factory_reset(self, exclusion: int = 0) -> None:
  #   """Reset ALL settings to factory defaults. (FSET) DESTRUCTIVE."""
  #   await self.send_command(f"FSET {exclusion}")

  # # Commented out - require physical interaction or architecture changes # #
  #
  # @requires_mt_sics_command("C1")
  # async def start_adjustment(self) -> List[MettlerToledoResponse]:
  #   """Start adjustment. (C1) Moves internal calibration weights."""
  #   return await self.send_command("C1")
  #
  # @requires_mt_sics_command("C2")
  # async def start_adjustment_external_weight(self) -> List[MettlerToledoResponse]:
  #   """Adjust with external weight. (C2) Requires placing calibration weight."""
  #   return await self.send_command("C2")
  #
  # @requires_mt_sics_command("C3")
  # async def start_adjustment_builtin_weight(self) -> List[MettlerToledoResponse]:
  #   """Adjust with built-in weight. (C3) Moves internal weights."""
  #   return await self.send_command("C3")
  #
  # @requires_mt_sics_command("TST1")
  # async def start_test(self) -> List[MettlerToledoResponse]:
  #   """Run test according to current settings. (TST1) Moves internal weights."""
  #   return await self.send_command("TST1")
  #
  # @requires_mt_sics_command("TST2")
  # async def start_test_external_weight(self) -> List[MettlerToledoResponse]:
  #   """Run test with external weight. (TST2) Requires placing test weight."""
  #   return await self.send_command("TST2")
  #
  # @requires_mt_sics_command("TST3")
  # async def start_test_builtin_weight(self) -> List[MettlerToledoResponse]:
  #   """Run test with built-in weight. (TST3) Moves internal weights."""
  #   return await self.send_command("TST3")
  #
  # @requires_mt_sics_command("SIR")
  # async def read_weight_immediately_repeat(self) -> ...:
  #   """Stream weight values at update rate. (SIR) Needs async iterator."""
  #   ...
  #
  # @requires_mt_sics_command("SR")
  # async def read_stable_weight_repeat(self) -> ...:
  #   """Stream stable weight on change. (SR) Needs async iterator."""
  #   ...
