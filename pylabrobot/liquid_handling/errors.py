""" Various errors that can be raised by a liquid handling system. """


class NoChannelError(Exception):
  """ Raised when no channel is available, e.g. when trying to pick up a tip with no empty channels.
  This error is only raised when the channel is automatically selected by the system.

  Examples:
  - when trying to pick up a tip with no empty channels
  """


class NoTipError(Exception):
  """ Raised when a tip is not present while it is required.

  Examples:
  - when trying to aspirate liquid
  - when trying to discard
  - when trying to pick up a tip, but no tip is present in the tip rack
  """

class HasTipError(Exception):
  """ Raised when a tip is present while it is not required.

  Examples:
  - when trying to pick up a tip with a tip already present
  - when to discard a tip to a location in a tip rack where a tip is already present
  """


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
