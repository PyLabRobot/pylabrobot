from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pylabrobot.resources.volume_tracker import VolumeTracker


@dataclass
class Tip:
  """A single tip.

  Attributes:
    has_filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in in mm
    maximal_volume: maximal volume of the tip, in ul
    fitting_depth: the overlap between the tip and the pipette, in mm
    name: optional identifier for this individual tip
  """

  has_filter: bool
  total_tip_length: float
  maximal_volume: float
  fitting_depth: float
  name: Optional[str] = None

  def __post_init__(self):
    thing = self.name or "tip_tracker"
    self.tracker = VolumeTracker(thing=thing, max_volume=self.maximal_volume)

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "name": self.name,
      "total_tip_length": self.total_tip_length,
      "has_filter": self.has_filter,
      "maximal_volume": self.maximal_volume,
      "fitting_depth": self.fitting_depth,
    }

  def __hash__(self):
    return hash(repr(self))

  def __eq__(self, other: object) -> bool:
    if not isinstance(other, Tip):
      return NotImplemented

    return (
      self.has_filter == other.has_filter
      and self.total_tip_length == other.total_tip_length
      and self.maximal_volume == other.maximal_volume
      and self.fitting_depth == other.fitting_depth
    )


TipCreator = Callable[[], Tip]
