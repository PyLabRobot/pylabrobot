from abc import ABC, abstractmethod
import contextlib
import sys
from typing import List, TYPE_CHECKING

from pylabrobot.resources.errors import (
  ContainerTooLittleLiquidError,
  ContainerTooLittleVolumeError,
  TipTooLittleLiquidError,
  TipTooLittleVolumeError,
)

if TYPE_CHECKING:
  from pylabrobot.liquid_handling.standard import LiquidHandlingOp, Aspiration, Dispense


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


class VolumeTracker(ABC):
  """ A volume tracker tracks operations that change the volume in a container and raises errors
  if the volume operations are invalid. """

  def __init__(self, max_volume: float):
    self._ops: List["LiquidHandlingOp"] = []
    self.pending: List["LiquidHandlingOp"] = []
    self._is_disabled = False
    self._current_volume = 0.0
    self._pending_volume = 0.0
    self.max_volume = max_volume

  @property
  def is_disabled(self) -> bool:
    return self._is_disabled

  def disable(self) -> None:
    """ Disable the volume tracker. """
    self._is_disabled = True

  def enable(self) -> None:
    """ Enable the volume tracker. """
    self._is_disabled = False

  def set_used_volume(self, volume: float) -> None:
    """ Set the volume of the container. """
    self.pending.clear()
    self._current_volume = volume
    self._pending_volume = volume

  @property
  def history(self) -> List["LiquidHandlingOp"]:
    """ The past operations. """
    return self._ops

  @abstractmethod
  def handle_aspiration(self, op: "Aspiration") -> None:
    """ Update the pending state with the operation. """

  @abstractmethod
  def handle_dispense(self, op: "Dispense") -> None:
    """ Update the pending state with the operation. """

  def queue_aspiration(self, op: "Aspiration") -> None:
    """ Check if the operation is valid given the current state. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self.handle_aspiration(op)
    self.pending.append(op)

  def queue_dispense(self, op: "Dispense") -> None:
    """ Check if the operation is valid given the current state. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self.handle_dispense(op)
    self.pending.append(op)

  def get_used_volume(self) -> float:
    """ Get the used volume of the container. Note that this includes pending operations. """

    return self._pending_volume

  def get_free_volume(self) -> float:
    """ Get the free volume of the container. Note that this includes pending operations. """

    return self.max_volume - self.get_used_volume()

  def commit(self) -> None:
    """ Commit the pending operations. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self._current_volume = self._pending_volume
    self._ops += self.pending
    self.pending.clear()

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self._pending_volume = self._current_volume
    self.pending.clear()

  def clear(self) -> None:
    """ Clear the history. """
    self._ops.clear()
    self.pending.clear()
    self._current_volume = 0.0
    self._pending_volume = 0.0


class ContainerVolumeTracker(VolumeTracker):
  """ A container volume tracker tracks and validates volume operations for a single container. """

  def handle_aspiration(self, op: "Aspiration") -> None:
    if op.volume > self.get_used_volume():
      raise ContainerTooLittleLiquidError(f"Container {op.resource.name} has too little liquid to "
                                      f"aspirate {op.volume} uL ({self.get_used_volume()} uL out "
                                      f"of {self.max_volume} uL used).")
    self._pending_volume -= op.volume

  def handle_dispense(self, op: "Dispense") -> None:
    if op.volume > self.get_free_volume():
      raise ContainerTooLittleVolumeError(f"Container {op.resource.name} has too little volume to "
                                      f"dispense {op.volume} uL ({self.get_used_volume()} uL out "
                                      f"of {self.max_volume} uL used).")
    self._pending_volume += op.volume


class TipVolumeTracker(VolumeTracker):
  """ A channel volume tracker tracks and validates volume operations for a single tip. """

  def handle_aspiration(self, op: "Aspiration") -> None:
    if op.volume > self.get_free_volume():
      raise TipTooLittleVolumeError(f"Tip has too little volume to aspirate "
                                    f"{op.volume} uL ({self.get_used_volume()} uL out of "
                                    f"{self.max_volume} uL used).")

    self._pending_volume += op.volume

  def handle_dispense(self, op: "Dispense") -> None:
    if op.volume > self.get_used_volume():
      raise TipTooLittleLiquidError(f"Tip has too little liquid to dispense "
                                    f"{op.volume} uL ({self.get_used_volume()} uL out of "
                                    f"{self.max_volume} uL used).")

    self._pending_volume -= op.volume
