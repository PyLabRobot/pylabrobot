"""Errors raised by the FilterMax F5 driver."""

from __future__ import annotations


class FilterMaxError(Exception):
  """Base class for FilterMax driver errors."""


class FilterMaxProtocolError(FilterMaxError):
  """The instrument returned malformed or unexpected protocol data."""


class FilterMaxChecksumError(FilterMaxProtocolError):
  """An ASTM frame checksum did not match its payload."""


class FilterMaxTimeoutError(FilterMaxProtocolError, TimeoutError):
  """The instrument did not complete a protocol step before its deadline."""


class FilterMaxIdentityError(FilterMaxError):
  """The serial port did not identify as a FilterMax F5."""


class FilterMaxCatalogError(FilterMaxError):
  """The filter-slide catalog is absent, invalid, or ambiguous."""


class FilterMaxFilterNotFoundError(FilterMaxCatalogError):
  """No compatible filter matches a requested measurement."""


class FilterMaxAmbiguousFilterError(FilterMaxCatalogError):
  """More than one compatible filter matches a requested measurement."""


class FilterMaxSlideNotInstalledError(FilterMaxCatalogError):
  """A requested filter slide is not installed."""


class FilterMaxUnsupportedOperationError(FilterMaxError):
  """The requested operation has not been verified for this hardware configuration."""


class FilterMaxDeviceError(FilterMaxError):
  """An error reported by the FilterMax firmware."""

  def __init__(self, code: int, detail: str, command: str):
    """Preserve the firmware code, detail text, and failed request."""
    self.code = code
    self.detail = detail
    self.command = command
    super().__init__(f"FilterMax command {command!r} failed with E{code}: {detail}")


class FilterMaxReadCancelled(FilterMaxDeviceError):
  """The active read was cancelled by a STOP request (device code E140)."""
