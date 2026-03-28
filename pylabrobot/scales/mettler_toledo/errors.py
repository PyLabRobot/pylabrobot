"""MT-SICS error types and response codes (spec Sections 2.1.3.1 - 2.1.3.3)."""

from typing import Optional


class MettlerToledoError(Exception):
  """Exceptions raised by a Mettler Toledo scale."""

  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title = title
    self.message = message

  def __str__(self) -> str:
    return f"{self.title}: {self.message}"

  # -- General errors (spec Section 2.1.3.2) --

  @staticmethod
  def unknown_error() -> "MettlerToledoError":
    return MettlerToledoError(title="Unknown error", message="An unknown error occurred")

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

  # -- Command-specific status codes (spec Section 2.1.3.1) --

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

  # -- Weight response error codes (spec Section 2.1.3.3) --

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
