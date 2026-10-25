"""Errors of BMG Labtech's wire framing."""


class FrameError(Exception):
  """A reply frame is malformed, truncated, or missing."""


class ChecksumError(FrameError):
  """An FTDI reply frame's checksum does not match its bytes."""
