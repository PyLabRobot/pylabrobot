""" Various errors that can be raised by a liquid handling system. """


class NoChannelError(Exception):
  """ Raised when no channel is available, e.g. when trying to pick up a tip with no empty channels.
  This error is only raised when the channel is automatically selected by the system.

  Examples:
  - when trying to pick up a tip with no empty channels
  """
