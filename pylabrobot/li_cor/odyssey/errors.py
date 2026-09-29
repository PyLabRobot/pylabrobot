"""Errors reported while communicating with the Odyssey Classic."""


class OdysseyError(RuntimeError):
  """An Odyssey HTTP request or protocol operation failed."""


class OdysseyScanError(OdysseyError):
  """Scan configuration or acquisition failed."""


class OdysseyImageError(OdysseyError):
  """An image could not be retrieved completely."""


class OdysseyStatusError(OdysseyError):
  """The instrument status could not be read or interpreted."""
