class ChannelsDoNotFitError(Exception):
  """Raised when channels cannot be positioned within a resource's compartments while respecting
  no-go zones and spacing constraints."""
