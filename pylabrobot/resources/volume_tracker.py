from abc import ABC, abstractmethod
import contextlib
import sys
from typing import List, Tuple, Optional

from pylabrobot.resources.errors import (
  ContainerTooLittleLiquidError,
  ContainerTooLittleVolumeError,
  TipTooLittleLiquidError,
  TipTooLittleVolumeError,
)

from pylabrobot.liquid_handling.standard import LiquidHandlingOp, Aspiration, Dispense
from pylabrobot.liquid_handling.liquid_classes.abstract import Liquid


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
    self.max_volume = max_volume

    self.liquids: List[Tuple[Optional[Liquid], float]] = []
    self.pending_liquids: List[Tuple[Optional[Liquid], float]] = []

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
    self.pending_liquids.clear()

  @property
  def _current_volume(self) -> float:
    return sum(volume for _, volume in self.liquids)

  @property
  def _pending_volume(self) -> float:
    return sum(volume for _, volume in self.pending_liquids) + self._current_volume

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
    self._ops += self.pending
    self.pending.clear()

  def rollback(self) -> None:
    """ Rollback the pending operations. """
    assert not self.is_disabled, "Volume tracker is disabled. Call `enable()`."
    self.pending.clear()

  def clear(self) -> None:
    """ Clear the history. """
    self._ops.clear()
    self.pending.clear()
    self.liquids.clear()
    self.pending_liquids.clear()

  def serialize(self) -> dict:
    """ Serialize the volume tracker. """

    def serialize_liquid_or_none(liquid: Optional["Liquid"]) -> Optional[str]:
      return liquid.serialize() if liquid is not None else None

    def serialize_op(op: "LiquidHandlingOp") -> dict:
      return {
        "type": op.__class__.__name__,
        "volume": op.volume,
      }

    return {
      "history": [serialize_op(op) for op in self.history],
      "pending": [serialize_op(op) for op in self.pending],
      "liquids": [(serialize_liquid_or_none(l), v) for l, v in self.liquids],
      "pending_liquids": [(serialize_liquid_or_none(l), v) for l, v in self.pending_liquids],
    }

  def load_state(self, state: dict) -> None:
    """ Load the state of the volume tracker. """

    def load_liquid_or_none(liquid: Optional[str]) -> Optional["Liquid"]:
      return Liquid.deserialize(liquid) if liquid is not None else None

    def load_op(op_data: dict) -> "LiquidHandlingOp":
      op_data_copy = op_data.copy()
      op_type = op_data_copy.pop("type")
      if op_type == "Aspiration":
        return Aspiration(**op_data_copy)
      elif op_type == "Dispense":
        return Dispense(**op_data_copy)
      else:
        raise ValueError(f"Unknown op type: {op_type}")

    self._ops = [load_op(op) for op in state["history"]]
    self.pending = [load_op(op) for op in state["pending"]]
    self.liquids = [(load_liquid_or_none(l), v) for l, v in state["liquids"]]
    self.pending_liquids = [(load_liquid_or_none(l), v) for l, v in state["pending_liquids"]]


class ContainerVolumeTracker(VolumeTracker):
  """ A container volume tracker tracks and validates volume operations for a single container. """

  def handle_aspiration(self, op: "Aspiration") -> None:
    if op.volume > self.get_used_volume():
      raise ContainerTooLittleLiquidError(f"Container {op.resource.name} has too little liquid to "
                                      f"aspirate {op.volume} uL ({self.get_used_volume()} uL out "
                                      f"of {self.max_volume} uL used).")

    # remove liquids top to bottom
    aspirated_volume = 0.0
    while aspirated_volume < op.volume:
      liquid, volume = self.liquids.pop()
      aspirated_volume += volume

      # if we have more liquid than we need, put the excess back
      if aspirated_volume > op.volume:
        self.liquids.append((liquid, aspirated_volume - op.volume))

  def handle_dispense(self, op: "Dispense") -> None:
    if op.volume > self.get_free_volume():
      raise ContainerTooLittleVolumeError(f"Container {op.resource.name} has too little volume to "
                                      f"dispense {op.volume} uL ({self.get_used_volume()} uL out "
                                      f"of {self.max_volume} uL used).")

    self.liquids.append((op.liquid, op.volume))


class TipVolumeTracker(VolumeTracker):
  """ A channel volume tracker tracks and validates volume operations for a single tip. """

  def handle_aspiration(self, op: "Aspiration") -> None:
    if op.volume > self.get_free_volume():
      raise TipTooLittleVolumeError(f"Tip has too little volume to aspirate "
                                    f"{op.volume} uL ({self.get_used_volume()} uL out of "
                                    f"{self.max_volume} uL used).")

    self.pending_liquids.append((op.liquid, op.volume))

  def handle_dispense(self, op: "Dispense") -> None:
    if op.volume > self.get_used_volume():
      raise TipTooLittleLiquidError(f"Tip has too little liquid to dispense "
                                    f"{op.volume} uL ({self.get_used_volume()} uL out of "
                                    f"{self.max_volume} uL used).")

    # remove liquids top to bottom
    dispensed_volume = 0.0
    while dispensed_volume < op.volume:
      liquid, volume = self.pending_liquids.pop()
      dispensed_volume += volume

      # if we have more liquid than we need, put the excess back
      if dispensed_volume > op.volume:
        self.pending_liquids.append((liquid, dispensed_volume - op.volume))
