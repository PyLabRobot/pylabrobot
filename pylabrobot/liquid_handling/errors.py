from typing import Dict


class NoChannelError(Exception):
  """ Raised when no channel is available, e.g. when trying to pick up a tip with no empty channels.
  This error is only raised when the channel is automatically selected by the system.

  Examples:
  - when trying to pick up a tip with no empty channels available on a robot
  """


class ChannelizedError(Exception):
  """ Raised by operations that work on multiple channels: pick_up_tips, drop_tips, aspirate, and
  dispense. Contains a key for each channel that had an error, and the error that occurred. """

  def __init__(self, errors: Dict[int, Exception], **kwargs):
    self.errors = errors
    self.kwargs = kwargs

  def __str__(self) -> str:
    kwarg_string = ", ".join([f"{k}={v}" for k, v in self.kwargs.items()])
    return f"ChannelizedError(errors={self.errors}, {kwarg_string})"

  def __len__(self) -> int:
    return len(self.errors)
