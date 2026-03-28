"""Mettler Toledo scale backend using the MT-SICS (Mettler Toledo Standard Interface Command Set) serial protocol."""

# similar library: https://github.com/janelia-pypi/mettler_toledo_device_python

import asyncio
import functools
import logging
import time
import warnings
from typing import Any, Callable, List, Literal, Optional, Set, TypeVar, Union

from pylabrobot.io.serial import Serial
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.scales.mettler_toledo.errors import MettlerToledoError
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")

MettlerToledoResponse = List[str]


F = TypeVar("F", bound=Callable[..., Any])


def requires_mt_sics_level(level: int) -> Callable[[F], F]:
  """Decorator that gates a method on the connected device supporting the required MT-SICS level.

  During setup(), the backend queries I1 to discover which levels the connected device supports.
  Methods decorated with a level higher than what the device reports will raise MettlerToledoError.
  See the class docstring of MettlerToledoWXS205SDUBackend for level descriptions.
  """

  def decorator(func: F) -> F:
    func._mt_sics_level = level  # type: ignore[attr-defined]

    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
      if hasattr(self, "_mt_sics_levels") and self._mt_sics_levels is not None:
        if level not in self._mt_sics_levels:
          raise MettlerToledoError(
            title="Command not supported",
            message=f"'{func.__name__}' requires MT-SICS level {level}, "
            f"but device supports levels: {sorted(self._mt_sics_levels)}",
          )
      return await func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]

  return decorator


# TODO: rename to MTSICSDriver in v1.0.0-beta
class MettlerToledoWXS205SDUBackend(ScaleBackend):
  """Backend for Mettler Toledo scales using the MT-SICS protocol.

  MT-SICS (Mettler Toledo Standard Interface Command Set) is the serial communication
  protocol used by Mettler Toledo's Automated Precision Weigh Modules. This backend is
  compatible with any MT-SICS device, including the WXS, WMS, and WX series. During
  setup(), this backend queries the device to discover its identity, capacity, and
  supported MT-SICS levels.

  MT-SICS levels:
    - Level 0: Basic set - identification (I0-I4), basic weighing (S, SI), zero (Z, ZI),
      tare (T, TI), cancel (@). Always available on every MT-SICS device.
    - Level 1: Elementary commands - display (D, DW), tare memory (TA, TAC), timed
      weighing (SC), timed zero/tare (ZC, TC). Always available.
    - Level 2: Extended command list - configuration (M21, COM), device info (I50, I47,
      I48). Model-dependent, not guaranteed on every device.
    - Level 3: Application-specific command set. Model-dependent.

  Methods requiring Level 2+ are decorated with ``@requires_mt_sics_level`` and will
  raise ``MettlerToledoError`` if the connected device does not support the required level.

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

    # Device discovery (Level 0 - always available)
    # Note: device_type and capacity both use I2 but are separate methods intentionally -
    # single-responsibility per method, the duplicate I2 round-trip during one-time setup is fine.
    self._mt_sics_levels: Set[int] = await self._query_mt_sics_levels()
    self.device_type = await self.request_device_type()
    self.capacity = await self.request_capacity()

    logger.info(
      "[scale] Connected: %s (S/N: %s, capacity: %.1f g, MT-SICS levels: %s)",
      self.device_type,
      self.serial_number,
      self.capacity,
      sorted(self._mt_sics_levels),
    )

    # Set output unit to grams
    if 2 in self._mt_sics_levels:
      await self.set_host_unit_grams()

  async def stop(self) -> None:
    await self.io.stop()

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.io.port}

  # === Device discovery ===

  async def _query_mt_sics_levels(self) -> Set[int]:
    """Query supported MT-SICS levels via I1 command (Level 0 - always available).

    Returns a set of integers representing the supported levels (e.g. {0, 1, 2, 3}).
    """
    responses = await self.send_command("I1")
    # I1 A "0123" "2.00" "2.20" "1.00" "1.50"
    self._validate_response(responses[0], 3, "I1")
    level_string = responses[0][2].replace('"', "")
    return {int(c) for c in level_string}

  # === Response parsing ===

  @staticmethod
  def _validate_response(response: List[str], min_length: int, command: str) -> None:
    """Validate that a parsed response has the expected minimum number of fields.

    Raises:
      MettlerToledoError: if the response is too short.
    """
    if len(response) < min_length:
      raise MettlerToledoError(
        title="Unexpected response",
        message=f"Expected at least {min_length} fields for '{command}', "
        f"got {len(response)}: {' '.join(response)}",
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

  def _parse_basic_errors(self, response: List[str]) -> None:
    """Helper function for parsing basic errors that are common to many commands. If an error is
    detected, a 'MettlerToledoError' exception is raised.

    These are in the first place of the response:
      - ES: syntax error: The weigh module/balance has not recognized the received command or the
        command is not allowed
      - ET: transmission error: The weigh module/balance has received a "faulty" command, e.g. owing
        to a parity error or interface break
      - EL: logical error: The weigh module/balance can not execute the received command

    These are in the second place of the response (MT-SICS spec p.10, sec 2.1.3.1):
      - A: Command executed successfully
      - B: Command not yet terminated, additional responses following
      - I: Internal error (e.g. balance not ready yet)
      - L: Logical error (e.g. parameter not allowed)
      - +: Balance in overload range
      - -: Balance in underload range

    Note: B status (multi-response) is handled by send_command, which reads all lines
    until status A. Each line is validated through this method individually.
    """

    if len(response) == 0:
      raise MettlerToledoError(
        title="Empty response",
        message="Received empty response from scale",
      )

    # General error messages are single-token: ES, ET, EL
    if response[0] == "ES":
      raise MettlerToledoError.syntax_error()
    if response[0] == "ET":
      raise MettlerToledoError.transmission_error()
    if response[0] == "EL":
      raise MettlerToledoError.logical_error()

    if len(response) < 2:
      raise MettlerToledoError(
        title="Unexpected response",
        message=f"Expected at least 2 fields, got: {' '.join(response)}",
      )

    if response[1] == "I":
      raise MettlerToledoError.executing_another_command()
    if response[1] == "L":
      raise MettlerToledoError.incorrect_parameter()
    if response[1] == "+":
      raise MettlerToledoError.overload()
    if response[1] == "-":
      raise MettlerToledoError.underload()

    if len(response) >= 4 and response[0] == "S" and response[1] == "S" and response[2] == "Error":
      error_code = response[3]
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

    logger.log(LOG_LEVEL_IO, "[scale] Sent command: %s", command)
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

      logger.log(LOG_LEVEL_IO, "[scale] Received response: %s", raw_response)
      response = raw_response.decode("utf-8").strip().split()
      self._parse_basic_errors(response)
      responses.append(response)  # type: ignore[arg-type]

      # Status B means more responses follow; anything else (A, etc.) is final
      if len(response) < 2 or response[1] != "B":
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
    return responses[0][2].replace('"', "")

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
    if responses[0][1] == "E":
      raise MettlerToledoError(
        title="Error while canceling",
        message=f"C command returned error: {responses[0]}",
      )

  # # Identification commands # #

  async def request_serial_number(self) -> str:
    """Get the serial number of the scale. (I4 command)"""
    responses = await self.send_command("I4")
    self._validate_response(responses[0], 3, "I4")
    return responses[0][2].replace('"', "")

  async def request_device_type(self) -> str:
    """Query the device type string. (I2 command)"""
    responses = await self.send_command("I2")
    # After split(): ["I2", "A", '"WXS205SDU"', "220.0000", "g"]
    self._validate_response(responses[0], 5, "I2")
    return responses[0][2].replace('"', "")

  async def request_capacity(self) -> float:
    """Query the maximum weighing capacity in grams. (I2 command)"""
    responses = await self.send_command("I2")
    # After split(): ["I2", "A", '"WXS205SDU"', "220.0000", "g"]
    self._validate_response(responses[0], 5, "I2")
    self._validate_unit(responses[0][4], "I2")
    return float(responses[0][3])

  @requires_mt_sics_level(2)
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
    self._validate_unit(responses[0][4], "I50")
    return float(responses[0][3])

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
    self._validate_unit(responses[0][3], "TA")
    return float(responses[0][2])

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
    self._validate_unit(responses[0][3], "S")
    return float(responses[0][2])

  async def read_dynamic_weight(self, timeout: float) -> float:
    """Read a stable weight value from the machine within a given timeout, or
    return the current weight value if not possible. (MEASUREMENT command)

    Args:
      timeout: The timeout in seconds.
    """

    timeout = int(timeout * 1000)  # convert to milliseconds

    responses = await self.send_command(f"SC {timeout}")
    self._validate_response(responses[0], 4, "SC")
    self._validate_unit(responses[0][3], "SC")
    return float(responses[0][2])

  async def read_weight_value_immediately(self) -> float:
    """Read a weight value immediately from the scale. (MEASUREMENT command)

    "Use SI to immediately send the current weight value, along with the host unit, from the
    balance to the connected communication partner via the interface."
    """

    responses = await self.send_command("SI")
    self._validate_response(responses[0], 4, "SI")
    self._validate_unit(responses[0][3], "SI")
    return float(responses[0][2])

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

  @requires_mt_sics_level(2)
  async def set_host_unit_grams(self) -> List[MettlerToledoResponse]:
    """Set the host output unit to grams. (M21 command)"""
    return await self.send_command("M21 0 0")

  # # # Deprecated alias with warning # # #

  # TODO: remove after 2026-03

  async def get_serial_number(self) -> str:
    """Deprecated: Use request_serial_number() instead."""
    warnings.warn(
      "get_serial_number() is deprecated and will be removed in 2026-03. "
      "Use request_serial_number() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.request_serial_number()

  async def get_tare_weight(self) -> float:
    """Deprecated: Use request_tare_weight() instead."""
    warnings.warn(
      "get_tare_weight() is deprecated and will be removed in 2026-03. "
      "Use request_tare_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.request_tare_weight()

  async def get_stable_weight(self) -> float:
    """Deprecated: Use read_stable_weight() instead."""
    warnings.warn(
      "get_stable_weight() is deprecated and will be removed in 2026-03. "
      "Use read_stable_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_stable_weight()

  async def get_dynamic_weight(self, timeout: float) -> float:
    """Deprecated: Use read_dynamic_weight() instead."""
    warnings.warn(
      "get_dynamic_weight() is deprecated and will be removed in 2026-03. "
      "Use read_dynamic_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_dynamic_weight(timeout)

  async def get_weight_value_immediately(self) -> float:
    """Deprecated: Use read_weight_value_immediately() instead."""
    warnings.warn(
      "get_weight_value_immediately() is deprecated and will be removed in 2026-03. "
      "Use read_weight_value_immediately() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_weight_value_immediately()

  async def get_weight(self, timeout: Union[Literal["stable"], float, int] = "stable") -> float:
    """Deprecated: Use read_weight() instead."""
    warnings.warn(
      "get_weight() is deprecated and will be removed in 2026-03. Use read_weight() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    return await self.read_weight(timeout)
