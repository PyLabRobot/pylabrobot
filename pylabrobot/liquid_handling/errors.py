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
  """ Raised when a channel has no tip, e.g. when trying to discard a tip with a channel that does
  not have a tip. """


class TipSpotHasTipError(Exception):
  """ Raised when a tip spot has a tip, e.g. when trying to discard a tip with a tip spot that has a
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


class TooLittleLiquidError(Exception):
  """ Raised when not enough liquid is present in a container (tip/well/...) to perform an
  operation.

  Examples:
  - when trying to aspirate more liquid than the well contains
  - when trying to dispense more liquid than the pipette tip contains
  """
