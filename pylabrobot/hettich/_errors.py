"""Hettich protocol and workflow exceptions."""

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
