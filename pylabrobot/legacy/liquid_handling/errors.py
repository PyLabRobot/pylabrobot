"""Errors raised by the legacy liquid handler.

`ChannelsDoNotFitError` moved to `pylabrobot.utils.liquid_handling.errors`: it is raised by the
channel planning every multi-channel pipette device shares. It is still forwarded from here, as the
very same class, but warns.
"""

import warnings
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:  # the name this module forwards, for type checkers and IDEs
  from pylabrobot.utils.liquid_handling.errors import ChannelsDoNotFitError  # noqa: F401


class NoChannelError(Exception):
  """Raised when no channel is available, e.g. when trying to pick up a tip with no empty channels.
  This error is only raised when the channel is automatically selected by the system.

  Examples:
  - when trying to pick up a tip with no empty channels available on a robot
  """


class ChannelizedError(Exception):
  """Raised by operations that work on multiple channels: pick_up_tips, drop_tips, aspirate, and
  dispense. Contains a key for each channel that had an error, and the error that occurred."""

  def __init__(self, errors: Dict[int, Exception], **kwargs):
    self.errors = errors
    self.kwargs = kwargs

  def __str__(self) -> str:
    kwarg_string = ", ".join([f"{k}={v}" for k, v in self.kwargs.items()])
    return f"ChannelizedError(errors={self.errors}, {kwarg_string})"

  def __len__(self) -> int:
    return len(self.errors)


# TODO: Remove >2026-12
def __getattr__(name: str) -> Any:
  if name == "ChannelsDoNotFitError":
    warnings.warn(
      "pylabrobot.legacy.liquid_handling.errors.ChannelsDoNotFitError is deprecated and will be "
      "removed in the future. It moved to pylabrobot.utils.liquid_handling.errors; update your "
      "import to `from pylabrobot.utils.liquid_handling.errors import ChannelsDoNotFitError`.",
      DeprecationWarning,
      stacklevel=2,
    )
    from pylabrobot.utils.liquid_handling import errors

    return errors.ChannelsDoNotFitError
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
