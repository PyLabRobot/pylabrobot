"""EL406 exception classes.

This module contains exception classes used by the BioTek EL406
plate washer backend.
"""

from __future__ import annotations


class EL406CommunicationError(Exception):
  """Exception raised for FTDI/USB communication errors with the EL406.

  This exception is raised when low-level communication fails, such as:
  - USB device disconnected
  - FTDI driver errors
  - Write/read failures

  Attributes:
    operation: The operation that failed (e.g., "write", "read", "open").
    original_error: The underlying exception that caused this error.
  """

  def __init__(
    self,
    message: str,
    operation: str = "",
    original_error: Exception | None = None,
  ) -> None:
    super().__init__(message)
    self.operation = operation
    self.original_error = original_error


class EL406DeviceError(Exception):
  """Exception raised when the EL406 device reports an error via the validity field.

  The device returns a non-zero validity code in the status poll response
  when a step command fails (e.g., no buffer fluid, invalid syringe, hardware fault).

  Attributes:
    error_code: The raw error code from the device (e.g., 0x1500).
    message: Human-readable error description.
  """

  def __init__(self, error_code: int, message: str) -> None:
    self.error_code = error_code
    super().__init__(f"EL406 error 0x{error_code:04X}: {message}")
