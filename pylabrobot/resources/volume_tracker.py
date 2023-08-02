# from abc import ABC, abstractmethod
import contextlib
import sys
from typing import List, Tuple, Optional, cast

from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError
from pylabrobot.resources.liquid import Liquid
from pylabrobot.serializer import serialize, deserialize


this = sys.modules[__name__]
this.volume_tracking_enabled = False # type: ignore

def set_volume_tracking(enabled: bool):
  this.volume_tracking_enabled = enabled # type: ignore

def does_volume_tracking() -> bool:
  return this.volume_tracking_enabled # type: ignore

@contextlib.contextmanager
def no_volume_tracking():
  old_value = this.volume_tracking_enabled
  this.volume_tracking_enabled = False # type: ignore
  yield
  this.volume_tracking_enabled = old_value # type: ignore


class VolumeTracker:
  """ A volume tracker tracks operations that change the volume in a container and raises errors
  if the volume operations are invalid. """

  def __init__(
    self,
    max_volume: float,
    liquids: Optional[List[Tuple[Optional[Liquid], float]]] = None,
    pending_liquids: Optional[List[Tuple[Optional[Liquid], float]]] = None
  ) -> None:
    self._is_disabled = False
    self.max_volume = max_volume

    self.liquids: List[Tuple[Optional[Liquid], float]] = liquids or []
    self.pending_liquids: List[Tuple[Optional[Liquid], float]] = pending_liquids or []

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  def disable(self) -> None:
    """ Disable the volume tracker. """
    self._is_disabled = True

  def enable(self) -> None:
    """ Enable the volume tracker. """
    self._is_disabled = False

  def set_liquids(self, liquids: List[Tuple[Optional["Liquid"], float]]) -> None:
    """ Set the liquids in the container. """
    self.liquids = liquids
    self.pending_liquids = liquids

  def remove_liquid(self, volume: float) -> None:
    """ Remove liquid from the container. Top to bottom. """

    if volume > self.get_used_volume():
      raise TooLittleLiquidError(
        f"Container has too little liquid: {volume}uL > {self.get_used_volume()}uL.")

    removed_volume = 0.0
    while removed_volume < volume:
      liquid, volume = self.liquids.pop()
      removed_volume += volume

      # If we have more liquid than we need, put the excess back.
      if removed_volume > volume:
        self.liquids.append((liquid, removed_volume - volume))

  def add_liquid(self, liquid: Optional["Liquid"], volume: float) -> None:
    """ Add liquid to the container. """

    if volume > self.get_free_volume():
      raise TooLittleVolumeError(
        "Container has too little volume: {volume}uL > {self.get_free_volume()}uL.")

    # If the last liquid is the same as the one we want to add, just add the volume to it.
    if len(self.pending_liquids) > 0:
      last_pending_liquid_tuple = self.pending_liquids[-1]
      if last_pending_liquid_tuple[0] == liquid:
        self.pending_liquids[-1] = (liquid, last_pending_liquid_tuple[1] + volume)
    else:
      self.pending_liquids.append((liquid, volume))

  def get_used_volume(self) -> float:
    """ Get the used volume of the container. Note that this includes pending operations. """
    return sum(volume for _, volume in self.pending_liquids)

  def get_free_volume(self) -> float:
    """ Get the free volume of the container. Note that this includes pending operations. """

    return self.max_volume - self.get_used_volume()

  def commit(self) -> None:
    """ Commit the pending operations. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."

    self.liquids = self.pending_liquids

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self.pending_liquids.clear()

  def serialize(self) -> dict:
    """ Serialize the volume tracker. """

    return {
      "liquids": [serialize(l) for l in self.liquids],
      "pending_liquids": [serialize(l) for l in self.pending_liquids],
    }

  def load_state(self, state: dict) -> None:
    """ Load the state of the volume tracker. """

    def load_liquid(data) -> Tuple[Optional["Liquid"], float]:
      return cast(Tuple["Liquid", float], tuple(deserialize(data)))

    self.liquids = [load_liquid(l) for l in state["liquids"]]
    self.pending_liquids = [load_liquid(l) for l in state["pending_liquids"]]
