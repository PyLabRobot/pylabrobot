import contextlib
import copy
import sys
import warnings
from typing import Callable, List, Optional, Tuple

from pylabrobot.resources.errors import (
  TooLittleLiquidError,
  TooLittleVolumeError,
)
from pylabrobot.resources.liquid import Liquid  # Keep Liquid for backward compatibility for now
from pylabrobot.serializer import deserialize, serialize

this = sys.modules[__name__]
this.volume_tracking_enabled = False  # type: ignore


def set_volume_tracking(enabled: bool):
  this.volume_tracking_enabled = enabled  # type: ignore


def does_volume_tracking() -> bool:
  return this.volume_tracking_enabled  # type: ignore


@contextlib.contextmanager
def no_volume_tracking():
  old_value = this.volume_tracking_enabled
  this.volume_tracking_enabled = False  # type: ignore
  yield
  this.volume_tracking_enabled = old_value  # type: ignore


VolumeTrackerCallback = Callable[[], None]


class VolumeTracker:
  """A volume tracker tracks operations that change the volume in a container and raises errors
  if the volume operations are invalid."""

  def __init__(
    self,
    thing: str,
    max_volume: float,
    initial_volume: Optional[float] = None,
  ) -> None:
    self._is_disabled = False
    self.thing = thing
    self.max_volume = max_volume
    self.volume = initial_volume or 0
    self.pending_volume = initial_volume or 0

    self._callback: Optional[VolumeTrackerCallback] = None

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  def disable(self) -> None:
    """Disable the volume tracker."""
    self._is_disabled = True

  def enable(self) -> None:
    """Enable the volume tracker."""
    self._is_disabled = False

  def set_volume(self, volume: float) -> None:
    """Set the volume in the container."""
    self.volume = volume
    self.pending_volume = volume

    if self._callback is not None:
      self._callback()

  def set_liquids(self, liquids: List[Tuple[Optional["Liquid"], float]]) -> None:
    """Set the liquids in the container.

    Deprecated:
    Use `set_volume` instead. This method will be removed in a future version.
    """
    warnings.warn(
      "`set_liquids` is deprecated and will be removed in a future version. "
      "Use `set_volume` instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.set_volume(sum(volume for _, volume in liquids))

  def remove_liquid(self, volume: float) -> None:
    """Remove liquid from the container."""

    if (volume - self.get_used_volume()) > 1e-6:
      raise TooLittleLiquidError(
        f"Not enough liquid in container: {volume}uL > {self.get_used_volume()}uL."
      )

    self.pending_volume -= volume

    if self._callback is not None:
      self._callback()

  def add_liquid(self, volume: float) -> None:
    """Add liquid to the container."""

    if (volume - self.get_free_volume()) > 1e-6:
      raise TooLittleVolumeError(
        f"Not enough space in container: {volume}uL > {self.get_free_volume()}uL."
      )

    self.pending_volume += volume

    if self._callback is not None:
      self._callback()

  def get_used_volume(self) -> float:
    """Get the used volume of the container. Note that this includes pending operations."""
    return self.pending_volume

  def get_free_volume(self) -> float:
    """Get the free volume of the container. Note that this includes pending operations."""
    return self.max_volume - self.get_used_volume()

  def get_liquids(self, top_volume: float) -> List[Tuple[Optional[Liquid], float]]:
    """Get the liquids in the top `top_volume` uL.

    Deprecated:
    This method is deprecated and will be removed in a future version.
    The volume tracker no longer tracks individual liquids.
    """
    warnings.warn(
      "`get_liquids` is deprecated and will be removed in a future version. "
      "The volume tracker no longer tracks individual liquids.",
      DeprecationWarning,
      stacklevel=2,
    )
    if (top_volume - self.get_used_volume()) > 1e-6:
      raise TooLittleLiquidError(f"Tracker only has {self.get_used_volume()}uL")

    return [(None, top_volume)]

  def commit(self) -> None:
    """Commit the pending operations."""
    assert not self.is_disabled, f"Volume tracker {self.thing} is disabled. Call `enable()`."
    self.volume = self.pending_volume

    if self._callback is not None:
      self._callback()

  def rollback(self) -> None:
    """Rollback the pending operations."""
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self.pending_volume = self.volume

  def serialize(self) -> dict:
    """Serialize the volume tracker."""
    return {
      "volume": self.volume,
      "pending_volume": self.pending_volume,
      "thing": self.thing,
      "max_volume": self.max_volume,
    }

  def load_state(self, state: dict) -> None:
    """Load the state of the volume tracker."""
    self.volume = state["volume"]
    self.pending_volume = state["pending_volume"]
    self.thing = state["thing"]
    self.max_volume = state["max_volume"]

  def register_callback(self, callback: VolumeTrackerCallback) -> None:
    self._callback = callback
