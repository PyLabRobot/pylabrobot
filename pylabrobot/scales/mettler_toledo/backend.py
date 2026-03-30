"""Mettler Toledo scale backend using the MT-SICS (Mettler Toledo Standard Interface Command Set) serial protocol."""

# similar library: https://github.com/janelia-pypi/mettler_toledo_device_python

import asyncio
import functools
import logging
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Optional, Set, TypeVar, Union

from pylabrobot.io.serial import Serial
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
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
    func._mt_sics_command = mt_sics_command  # type: ignore[attr-defined]

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

    return wrapper  # type: ignore[return-value]

  return decorator


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
    await self.io.setup()

    # Reset device to clean state (spec Section 2.2)
    # cancel() clears the input buffer and sends @, which returns the serial number
    self.serial_number = await self.cancel()

    # Discover supported commands via I0 (the definitive source per spec Section 2.2)
    self._supported_commands: Set[str] = await self._request_supported_commands()

    # Device identity (Level 0 - always available)
    # Note: device_type and capacity both use I2 but are separate methods intentionally -
    # single-responsibility per method, the duplicate I2 round-trip during one-time setup is fine.
    self.device_type = await self.request_device_type()
    self.capacity = await self.request_capacity()

    logger.info(
      "[MT Scale] Connected to Mettler Toledo scale on %s\n"
      "Device type: %s\n"
      "Serial number: %s\n"
      "Capacity: %.1f g\n"
      "Supported commands: %s",
      self.io.port,
      self.device_type,
      self.serial_number,
      self.capacity,
      sorted(self._supported_commands),
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
      await self.cancel()
    except Exception:
      logger.warning("[MT Scale] Could not reset device before disconnecting")
    logger.info("[MT Scale] Disconnected from %s", self.io.port)
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
    for name in dir(self):
      if name.startswith("_"):
        continue
      attr = getattr(type(self), name, None)
      if not callable(attr):
        continue
      # Check if the method has a command gate
      mt_cmd = getattr(attr, "_mt_sics_command", None)
      if mt_cmd is not None:
        if hasattr(self, "_supported_commands") and mt_cmd in self._supported_commands:
          supported.append(name)
        # If _supported_commands not yet populated (before setup), skip
      else:
        # Undecorated public methods are always available
        supported.append(name)
    return sorted(supported)

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

  # === Command Layer ===

  async def send_command(self, command: str, timeout: int = 60) -> List[MettlerToledoResponse]:
    """Send a command to the scale and read all response lines.

    Single-response commands (status A) return a list of one parsed line.
    Multi-response commands (status B) return all lines, reading until status A.

    Args:
      timeout: The timeout in seconds (applies across all response lines).
    """

    logger.log(LOG_LEVEL_IO, "[MT Scale] Sent command: %s", command)
    await self.io.write(command.encode() + b"\r\n")

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

      logger.log(LOG_LEVEL_IO, "[MT Scale] Received response: %s", raw_response)
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

  # === Public high-level API ===

  # # Cancel commands # #

  async def cancel(self) -> str:
    """@ - Reset the device to a determined state (spec Section 2.2).

    Equivalent to a power cycle: empties volatile memories, cancels all pending
    commands, resets key control to default. Tare memory is NOT reset.
    The cancel command is always executed, even when the device is busy.

    Returns the serial number from the I4-style response.
    """
    await self.io.reset_input_buffer()
    responses = await self.send_command("@")
    # @ responds with I4-style: I4 A "<SNR>"
    self._validate_response(responses[0], 3, "@")
    return responses[0].data[0]

  async def cancel_all(self) -> None:
    """C - Cancel all active and pending interface commands.

    Unlike cancel() (@), this does not reset the device - it only cancels
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

  # # Identification commands # #

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

  async def request_software_version(self) -> str:
    """Query the software version and type definition number. (I3 command)

    Returns the version string (e.g. "2.10 10.28.0.493.142").
    For bridge mode (no terminal), returns the bridge software version.
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
    individual scales in multi-device setups. Retained after @ cancel.
    """
    responses = await self.send_command("I10")
    self._validate_response(responses[0], 3, "I10")
    return responses[0].data[0]

  @requires_mt_sics_command("I10")
  async def set_device_id(self, device_id: str) -> None:
    """Set the user-assigned device identification string. (I10 command)

    Max 20 alphanumeric characters. Retained after @ cancel.
    Useful for labeling individual scales in multi-device setups.
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
  async def request_device_info(self) -> List[MettlerToledoResponse]:
    """Query detailed device information including all components. (I14 command)

    Returns multi-response with instrument configuration (No=0),
    descriptions (No=1), SW identification numbers (No=2),
    SW versions (No=3), serial numbers (No=4), and TDNR numbers (No=5).
    Each category reports data for bridge, terminal, options, etc.
    """
    return await self.send_command("I14")

  @requires_mt_sics_command("I15")
  async def request_uptime(self) -> dict:
    """Query the device uptime. (I15 command)

    Returns a dict with keys "days", "hours", "minutes", "seconds".
    Counts time since last power-on, including short interruptions.
    Not reset by @ cancel or restart.
    """
    responses = await self.send_command("I15")
    self._validate_response(responses[0], 6, "I15")
    return {
      "days": int(responses[0].data[0]),
      "hours": int(responses[0].data[1]),
      "minutes": int(responses[0].data[2]),
      "seconds": int(responses[0].data[3]),
    }

  @requires_mt_sics_command("DAT")
  async def request_date(self) -> str:
    """Query the current date from the device. (DAT command)

    Returns the date string as reported by the device.
    """
    responses = await self.send_command("DAT")
    self._validate_response(responses[0], 3, "DAT")
    return responses[0].data[0]

  @requires_mt_sics_command("TIM")
  async def request_time(self) -> str:
    """Query the current time from the device. (TIM command)

    Returns the time string as reported by the device.
    """
    responses = await self.send_command("TIM")
    self._validate_response(responses[0], 3, "TIM")
    return responses[0].data[0]

  @requires_mt_sics_command("I50")
  async def request_remaining_weighing_range(self) -> float:
    """Query remaining maximum weighing range in grams. (I50 command)

    Returns the remaining capacity accounting for all loads currently on the
    weighing platform (pre-load, tare, net load). A negative value means the
    maximum weighing range has been exceeded.

    I50 is a multi-response command: the device sends up to 3 lines
    (RangeNo 0 with B, RangeNo 1 with B, RangeNo 2 with A). We extract the
    value from the first response and drain the remaining ones.
    """
    responses = await self.send_command("I50")
    # responses[0]: I50 B 0 <Range> <Unit> (RangeNo 0 = max weighing range)
    # responses[1]: I50 B 1 <Range> <Unit> (RangeNo 1 = internal adjustment range)
    # responses[2]: I50 A 2 <Range> <Unit> (RangeNo 2 = external adjustment range)
    self._validate_response(responses[0], 5, "I50")
    self._validate_unit(responses[0].data[2], "I50")
    return float(responses[0].data[1])

  # # Zero commands # #

  async def zero_immediately(self) -> List[MettlerToledoResponse]:
    """Zero the scale immediately. (ACTION command)"""
    return await self.send_command("ZI")

  async def zero_stable(self) -> List[MettlerToledoResponse]:
    """Zero the scale when the weight is stable. (ACTION command)"""
    return await self.send_command("Z")

  async def zero_timeout(self, timeout: float) -> List[MettlerToledoResponse]:
    """Zero the scale after a given timeout. (ACTION command)"""
    # For some reason, this will always return a syntax error (ES), even though it should be allowed
    # according to the docs.
    timeout = int(timeout * 1000)
    return await self.send_command(f"ZC {timeout}")

  async def zero(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> List[MettlerToledoResponse]:
    """High level function to zero the scale. (ACTION command)

    Args:
      timeout: The timeout in seconds. If "stable", the scale will zero when the weight is stable.
        If 0, the scale will zero immediately. If a float/int, the scale will zero after the given
        timeout (in seconds).
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

  # # Tare commands # #

  async def tare_stable(self) -> List[MettlerToledoResponse]:
    """Tare the scale when the weight is stable. (ACTION command)"""
    return await self.send_command("T")

  async def tare_immediately(self) -> List[MettlerToledoResponse]:
    """Tare the scale immediately. (ACTION command)"""
    return await self.send_command("TI")

  async def tare_timeout(self, timeout: float) -> List[MettlerToledoResponse]:
    """Tare the scale after a given timeout. (ACTION command)"""
    # For some reason, this will always return a syntax error (ES), even though it should be allowed
    # according to the docs.
    timeout = int(timeout * 1000)  # convert to milliseconds
    return await self.send_command(f"TC {timeout}")

  async def tare(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> List[MettlerToledoResponse]:
    """High level function to tare the scale. (ACTION command)

    Args:
      timeout: The timeout in seconds. If "stable", the scale will tare when the weight is stable.
        If 0, the scale will tare immediately. If a float/int, the scale will tare after the given
        timeout (in seconds).
    """

    if timeout == "stable":
      # "Use T to tare the balance. The next stable weight value will be saved in the tare memory."
      return await self.tare_stable()

    if not isinstance(timeout, (float, int)):
      raise TypeError("timeout must be a float or 'stable'")

    if timeout < 0:
      raise ValueError("timeout must be greater than or equal to 0")

    if timeout == 0:
      return await self.tare_immediately()
    return await self.tare_timeout(timeout)

  # # Weight reading commands # #

  async def request_tare_weight(self) -> float:
    """Request tare weight value from scale's memory. (MEM-READ command)
    "Use TA to query the current tare value or preset a known tare value."
    """

    responses = await self.send_command("TA")
    self._validate_response(responses[0], 4, "TA")
    self._validate_unit(responses[0].data[1], "TA")
    return float(responses[0].data[0])

  async def clear_tare(self) -> List[MettlerToledoResponse]:
    """TAC - Clear tare weight value (MEM-WRITE command)"""
    return await self.send_command("TAC")

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

  async def read_dynamic_weight(self, timeout: float) -> float:
    """Read a stable weight value from the machine within a given timeout, or
    return the current weight value if not possible. (MEASUREMENT command)

    Args:
      timeout: The timeout in seconds.
    """

    timeout = int(timeout * 1000)  # convert to milliseconds

    responses = await self.send_command(f"SC {timeout}")
    self._validate_response(responses[0], 4, "SC")
    self._validate_unit(responses[0].data[1], "SC")
    return float(responses[0].data[0])

  async def read_weight_value_immediately(self) -> float:
    """Read a weight value immediately from the scale. (MEASUREMENT command)

    "Use SI to immediately send the current weight value, along with the host unit, from the
    balance to the connected communication partner via the interface."
    """

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

  # Commands for (optional) display manipulation

  async def set_display_text(self, text: str) -> List[MettlerToledoResponse]:
    """Set the display text of the scale. Return to the normal weight display with
    self.set_weight_display()."""
    return await self.send_command(f'D "{text}"')

  async def set_weight_display(self) -> List[MettlerToledoResponse]:
    """Return the display to the normal weight display."""
    return await self.send_command("DW")

  # # Configuration commands # #

  @requires_mt_sics_command("M21")
  async def set_host_unit_grams(self) -> List[MettlerToledoResponse]:
    """Set the host output unit to grams. (M21 command)"""
    return await self.send_command("M21 0 0")

  @requires_mt_sics_command("M28")
  async def measure_temperature(self) -> float:
    """Query the current temperature from the scale's internal sensor in degrees C. (M28 command)

    The number of temperature sensors depends on the product. This method returns
    the value from the first sensor. Useful for gravimetric verification where
    temperature affects liquid density and evaporation rate.
    """
    responses = await self.send_command("M28")
    # M28 A 1 22.5 (single sensor) or M28 B 1 22.5 ... M28 A 2 23.0 (multi-sensor)
    self._validate_response(responses[0], 4, "M28")
    return float(responses[0].data[1])
