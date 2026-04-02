# Re-export shared error classes from capabilities so that isinstance checks
# using the canonical capabilities types work correctly for legacy backends too.
from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError, NoChannelError


class ChannelsDoNotFitError(Exception):
  """Raised when channels cannot be positioned within a resource's compartments while respecting
  no-go zones and spacing constraints."""


__all__ = [
  "ChannelizedError",
  "ChannelsDoNotFitError",
  "NoChannelError",
]
