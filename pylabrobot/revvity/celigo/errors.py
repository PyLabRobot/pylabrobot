"""Exceptions shared by the Celigo controller components."""

from typing import Optional


class CeligoError(Exception):
  """Raised when the Celigo rejects a command or returns a malformed response."""

  def __init__(self, message: str, ack: Optional[int] = None) -> None:
    super().__init__(message)
    self.ack = ack
