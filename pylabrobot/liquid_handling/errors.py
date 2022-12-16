""" Various errors that can be raised by a liquid handling system. """


class NoChannelError(Exception):
  """ Raised when no channel is available, e.g. when trying to pick up a tip with no empty channels.
  This error is only raised when the channel is automatically selected by the system.

  Examples:
  - when trying to pick up a tip with no empty channels
  """


class ChannelHasTipError(Exception):
  """ Raised when a channel has a tip, e.g. when trying to pick up a tip with a channel that already
  has a tip. """


class ChannelHasNoTipError(Exception):
  """ Raised when a channel has no tip, e.g. when trying to drop a tip with a channel that does
  not have a tip. """


class TipSpotHasTipError(Exception):
  """ Raised when a tip spot has a tip, e.g. when trying to drop a tip with a tip spot that has a
  tip. """


class TipSpotHasNoTipError(Exception):
  """ Raised when a tip spot has no tip, e.g. when trying to pick up a tip with a tip spot that does
  not have a tip. """


class TooLittleVolumeError(Exception):
  """ Raised when the volume of a container (tip/well/...) the liquid is moving into is too small.

  Examples:
  - when trying to aspirate more liquid than the pipette tip can hold
  - when trying to dispense more liquid than the container can hold
  """


class TipTooLittleLiquidError(Exception):
  """ Raised when trying to dispense more liquid from a tip than is still present. """


class TipTooLittleVolumeError(Exception):
  """ Raised when trying to aspirate more liquid into a tip than is still available. """


class WellTooLittleLiquidError(Exception):
  """ Raised when trying to aspirate more liquid from a well than is still present. """


class WellTooLittleVolumeError(Exception):
  """ Raised when trying to dispense more liquid into a well than is still available. """

