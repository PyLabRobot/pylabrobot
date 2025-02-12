# similar library: https://github.com/janelia-pypi/mettler_toledo_device_python

import asyncio
import logging
import time
from typing import List, Literal, Optional, Union

from pylabrobot.io.serial import Serial
from pylabrobot.scales.scale_backend import ScaleBackend

logger = logging.getLogger("pylabrobot")


class MettlerToledoError(Exception):
  """Exceptions raised by a Mettler Toledo scale."""

  def __init__(self, title: str, message: Optional[str]) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}"

  @staticmethod
  def unknown_error() -> "MettlerToledoError":
    return MettlerToledoError(title="Unknown error", message="An unknown error occurred")

  @staticmethod
  def executing_another_command() -> "MettlerToledoError":
    return MettlerToledoError(
      title="Command not understood, not executable at present",
      message=(
        "Command understood but currently not executable (balance is "
        "currently executing another command)."
      ),
    )

  @staticmethod
  def incorrect_parameter() -> "MettlerToledoError":
    return MettlerToledoError(
      title="Command understood but not executable",
      message="(incorrect parameter).",
    )

  @staticmethod
  def overload() -> "MettlerToledoError":
    return MettlerToledoError(title="Balance in overload range.", message=None)

  @staticmethod
  def underload() -> "MettlerToledoError":
    return MettlerToledoError(title="Balance in underload range.", message=None)

  @staticmethod
  def syntax_error() -> "MettlerToledoError":
    return MettlerToledoError(
      title="Syntax error",
      message="The weigh module/balance has not recognized the received command or the command is "
      "not allowed",
    )

  @staticmethod
  def transmission_error() -> "MettlerToledoError":
    return MettlerToledoError(
      title="Transmission error",
      message="The weigh module/balance has received a 'faulty' command, e.g. owing to a parity "
      "error or interface break",
    )

  @staticmethod
  def logical_error() -> "MettlerToledoError":
    return MettlerToledoError(
      title="Logical error",
      message="The weigh module/balance can not execute the received command",
    )

  @staticmethod
  def boot_error(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Boot error",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def brand_error(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Brand error",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def checksum_error(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Checksum error",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def option_fail(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Option fail",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def eeprom_error(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="EEPROM error",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def device_mismatch(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Device mismatch",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def hot_plug_out(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Hot plug out",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def weight_module_electronic_mismatch(
    from_terminal: bool,
  ) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Weight module / electronic mismatch",
      message="from terminal" if from_terminal else "from electronics",
    )

  @staticmethod
  def adjustment_needed(from_terminal: bool) -> "MettlerToledoError":
    return MettlerToledoError(
      title="Adjustment needed",
      message="from terminal" if from_terminal else "from electronics",
    )


MettlerToledoResponse = List[str]


class MettlerToledoWXS205SDU(ScaleBackend):
  """Backend for the Mettler Toledo WXS205SDU scale.

  This scale is used by Hamilton in the liquid verification kit (LVK).

  Documentation: https://web.archive.org/web/20240208213802/https://www.mt.com/dam/
  product_organizations/industry/apw/generic/11781363_N_MAN_RM_MT-SICS_APW_en.pdf

  From the docs:

    "If several commands are sent in succession without waiting for the corresponding responses, it
    is possible that the weigh module/balance confuses the sequence of command processing or ignores
    entire commands."
  """

  def __init__(self, port: str) -> None:
    self.port = port
    self.io = Serial(self.port, baudrate=9600, timeout=1)

  async def setup(self) -> None:
    await self.io.setup()

    # set output unit to grams
    await self.send_command("M21 0 0")

  async def stop(self) -> None:
    await self.io.stop()

  def serialize(self) -> dict:
    return {**super().serialize(), "port": self.port}

  async def send_command(self, command: str, timeout: int = 60) -> MettlerToledoResponse:
    """Send a command to the scale and receive the response.

    Args:
      timeout: The timeout in seconds.
    """

    self.io.write(command.encode() + b"\r\n")

    raw_response = b""
    timeout_time = time.time() + timeout
    while True:
      raw_response = self.io.readline()
      await asyncio.sleep(0.001)
      if time.time() > timeout_time:
        raise TimeoutError("Timeout while waiting for response from scale.")
      if raw_response != b"":
        break
    logger.debug("[scale] Received response: %s", raw_response)
    response = raw_response.decode("utf-8").strip().split()

    # parse basic errors
    self._parse_basic_errors(response)

    # mypy doesn't understand this
    return response  # type: ignore

  def _parse_basic_errors(self, response: List[str]) -> None:
    """Helper function for parsing basic errors that are common to many commands. If an error is
    detected, a 'MettlerToledoError' exception is raised.

    These are in the first place of the response:
      - ES: syntax error: The weigh module/balance has not recognized the received command or the
        command is not allowed
      - ET: transmission error: The weigh module/balance has received a "faulty" command, e.g. owing
        to a parity error or interface break
      - EL: logical error: The weigh module/balance can not execute the received command

    These are in the second place of the response:
      - I: Command not understood, not executable at present
      - P: Command understood but not executable (incorrect parameter)
      - O: Balance in overload range
      - U: Balance in underload range
    """

    if response[0] == "ES":
      raise MettlerToledoError.syntax_error()
    if response[0] == "ET":
      raise MettlerToledoError.transmission_error()
    if response[0] == "EL":
      raise MettlerToledoError.logical_error()

    if response[1] == "I":
      raise MettlerToledoError.executing_another_command()
    if response[1] == "P":
      raise MettlerToledoError.incorrect_parameter()
    if response[1] == "+":
      raise MettlerToledoError.overload()
    if response[1] == "-":
      raise MettlerToledoError.underload()

    if response[0] == "S" and response[1] == "S" and response[2] == "Error":
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

  async def tare_stable(self) -> MettlerToledoResponse:
    """Tare the scale when the weight is stable."""
    return await self.send_command("T")

  async def tare_immediately(self) -> MettlerToledoResponse:
    """Tare the scale immediately."""
    return await self.send_command("TI")

  async def tare_timeout(self, timeout: float) -> MettlerToledoResponse:
    """Tare the scale after a given timeout."""
    # For some reason, this will always return a syntax error (ES), even though it should be allowed
    # according to the docs.
    timeout = int(timeout * 1000)  # convert to milliseconds
    return await self.send_command(f"TC {timeout}")

  async def tare(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> MettlerToledoResponse:
    """High level function to tare the scale.

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

  async def get_tare_weight(self) -> float:
    """TA - Tare weight value Description
    "Use TA to query the current tare value or preset a known tare value."
    """

    response = await self.send_command("TA")
    tare = float(response[2])
    unit = response[3]
    assert unit == "g"  # this is the format we expect
    return tare

  async def clear_tare(self) -> MettlerToledoResponse:
    """TAC - Clear tare weight value"""
    return await self.send_command("TAC")

  async def get_stable_weight(self) -> float:
    """Get a stable weight value from the scale.

    from the docs:

    "Use S to send a stable weight value, along with the host unit, from the balance to the
    connected communication partner via the interface. If the automatic door function is enabled and
    a stable weight is requested the balance will open and close the balance's doors to achieve a
    stable weight."
    """

    response = await self.send_command("S")
    weight = float(response[2])
    unit = response[3]
    assert unit == "g"  # this is the format we expect
    return weight

  async def get_dynamic_weight(self, timeout: float) -> float:
    """Get a stable weight value from the machine if possible within a given timeout, or return the
    current weight value if not possible.

    Args:
      timeout: The timeout in seconds.
    """

    timeout = int(timeout * 1000)  # convert to milliseconds

    response = await self.send_command(f"SC {timeout}")
    weight = float(response[2])
    unit = response[3]
    assert unit == "g"  # this is the format we expect
    return weight

  async def get_weight_value_immediately(self) -> float:
    """Get a weight value immediately from the scale.

    "Use SI to immediately send the current weight value, along with the host unit, from the balance
    to the connected communication partner via the interface."
    """

    response = await self.send_command("SI")
    weight = float(response[2])
    assert response[3] == "g"  # this is the format we expect
    return weight

  async def get_weight(self, timeout: Union[Literal["stable"], float, int] = "stable") -> float:
    """High level function to get a weight value from the scale.

    Args:
      timeout: The timeout in seconds. If "stable", the scale will return a weight value when the
        weight is stable. If 0, the scale will return a weight value immediately. If a float/int,
        the scale will return a weight value after the given timeout (in seconds).
    """

    if timeout == "stable":
      return await self.get_stable_weight()

    if not isinstance(timeout, (float, int)):
      raise TypeError("timeout must be a float or 'stable'")

    if timeout < 0:
      raise ValueError("timeout must be greater than or equal to 0")

    if timeout == 0:
      return await self.get_weight_value_immediately()

    return await self.get_dynamic_weight(timeout)

  async def get_serial_number(self) -> str:
    """Get the serial number of the scale."""
    response = await self.send_command("I4")
    serial_number = response[2]
    serial_number = serial_number.replace('"', "")
    return serial_number

  async def zero_immediately(self) -> MettlerToledoResponse:
    """Zero the scale immediately."""
    return await self.send_command("ZI")

  async def zero_stable(self) -> MettlerToledoResponse:
    """Zero the scale when the weight is stable."""
    return await self.send_command("Z")

  async def zero_timeout(self, timeout: float) -> MettlerToledoResponse:
    """Zero the scale after a given timeout."""
    # For some reason, this will always return a syntax error (ES), even though it should be allowed
    # according to the docs.
    timeout = int(timeout * 1000)
    return await self.send_command(f"ZC {timeout}")

  async def zero(
    self, timeout: Union[Literal["stable"], float, int] = "stable"
  ) -> MettlerToledoResponse:
    """High level function to zero the scale.

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

  async def set_display_text(self, text: str) -> MettlerToledoResponse:
    """Set the display text of the scale. Return to the normal weight display with
    self.set_weight_display()."""
    return await self.send_command(f'D "{text}"')

  async def set_weight_display(self) -> MettlerToledoResponse:
    """Return the display to the normal weight display."""
    return await self.send_command("DW")
