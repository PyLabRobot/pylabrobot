from __future__ import annotations


class MultidropCombiError(Exception):
  """Base exception for Multidrop Combi errors."""


class MultidropCombiCommunicationError(MultidropCombiError):
  """Serial communication failure (port not found, timeout, connection lost)."""

  def __init__(self, message: str, operation: str = "",
               original_error: Exception | None = None) -> None:
    self.operation = operation
    self.original_error = original_error
    super().__init__(message)


class MultidropCombiInstrumentError(MultidropCombiError):
  """Instrument returned a non-zero status code."""

  def __init__(self, status_code: int, description: str) -> None:
    self.status_code = status_code
    self.description = description
    super().__init__(f"Instrument error (status {status_code}): {description}")
