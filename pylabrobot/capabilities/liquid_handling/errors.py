"""Errors for liquid handling operations."""

from typing import Dict


class NoChannelError(Exception):
  """Raised when no channel is available."""


class BlowOutVolumeError(Exception):
  """Raised when blow-out air volume is invalid."""


class ChannelizedError(Exception):
  """Raised by multi-channel operations. Contains per-channel errors."""

  def __init__(self, errors: Dict[int, Exception], **kwargs):
    self.errors = errors
    self.kwargs = kwargs

  def __str__(self) -> str:
    kwarg_string = ", ".join([f"{k}={v}" for k, v in self.kwargs.items()])
    return f"ChannelizedError(errors={self.errors}, {kwarg_string})"

  def __len__(self) -> int:
    return len(self.errors)
