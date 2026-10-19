"""CLARIOstar error types, and the reply bytes that raise them."""

from typing import Dict, Optional, Tuple


class FrameError(Exception):
  """A reply frame is malformed, truncated, or missing."""


class ChecksumError(FrameError):
  """An FTDI reply frame's checksum does not match its bytes."""


# Reply byte 1 bits that put quit codes in bytes 6-10 of a type 0x01 reply.
REFUSED_BIT = 1 << 4
WARNING_BIT = 1 << 7


def _quit_codes(reply: bytes, bit: int) -> Optional[Tuple[int, int, int, int, int]]:
  """Return bytes 6-10 of a type 0x01 reply with `bit` set in byte 1, else None."""
  if len(reply) < 11 or reply[0] != 0x01 or not reply[1] & bit:
    return None
  q1, q2, q3, q4, q5 = reply[6:11]
  return (q1, q2, q3, q4, q5)


def parse_quit_codes(reply: bytes) -> Optional[Tuple[int, int, int, int, int]]:
  """Return the five quit codes of a refused command's reply, or None if it was not refused."""
  return _quit_codes(reply, REFUSED_BIT)


def parse_warning_codes(reply: bytes) -> Optional[Tuple[int, int, int, int, int]]:
  """Return the five codes of a device warning, or None if the reply carries none."""
  return _quit_codes(reply, WARNING_BIT)


# What the first quit code means, for the codes seen in replies from a reader. 1-3 mean the reader
# received a malformed frame.
QUIT_CODE_DESCRIPTIONS: Dict[int, str] = {
  1: "the reader received a frame with a wrong checksum",
  2: "the reader received a frame with a wrong end byte",
  3: "the reader received a frame with a wrong length",
  55: "the focal height is invalid",
  65: "the kinetic cycle time is too short",
  102: "no new measurement data is available yet",
  110: "the target temperature is out of range",
  201: "the reader is not initialized",
  202: "the reader is busy",
  213: "no measurement results are available",
  214: "no test run is active",
  250: "the motor controller reported a collision",
}


def describe_quit_codes(quit_codes: Tuple[int, int, int, int, int]) -> str:
  """Format quit codes as `code (description), quit codes q1/q2/q3/q4/q5`."""
  description = QUIT_CODE_DESCRIPTIONS.get(quit_codes[0], "no description known")
  return (
    f"quit code {quit_codes[0]} ({description}), quit codes {'/'.join(str(q) for q in quit_codes)}"
  )


class CommandRefusedError(Exception):
  """The reader refused a command and answered with quit codes."""

  def __init__(self, payload: bytes, quit_codes: Tuple[int, int, int, int, int]):
    """
    Args:
      payload: the logical payload that was refused.
      quit_codes: the five quit codes from the reply; the first names the reason.
    """
    self.payload = payload
    self.quit_codes = quit_codes
    super().__init__(f"CLARIOstar refused {payload.hex()}: {describe_quit_codes(quit_codes)}")
