from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Callable, Optional

from pylabrobot.resources.volume_tracker import VolumeTracker
from pylabrobot.serializer import SerializableMixin


@dataclass
class Tip(SerializableMixin):
  """A single tip.

  Attributes:
    has_filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in mm
    nominal_volume: rated working volume of the tip (what it is sold and named as), in uL.
      Defaults to maximal_volume when not given.
    maximal_volume: physical brim-full capacity of the tip, in uL
    fitting_depth: the overlap between the tip and the pipette, in mm
    name: optional identifier for this tip
  """

  has_filter: bool
  total_tip_length: float
  maximal_volume: float
  fitting_depth: float
  nominal_volume: Optional[float] = None
  name: Optional[str] = None

  def __post_init__(self):
    if self.name is None:
      warnings.warn(
        "Creating a Tip without a name is deprecated. "
        "Tips created from deck resources (e.g. TipSpot) should be named.",
        DeprecationWarning,
        stacklevel=2,
      )

    if self.nominal_volume is None:
      self.nominal_volume = self.maximal_volume

    thing = self.name or "tip_tracker"
    self.tracker = VolumeTracker(thing=thing, max_volume=self.maximal_volume)

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "name": self.name,
      "total_tip_length": self.total_tip_length,
      "has_filter": self.has_filter,
      "nominal_volume": self.nominal_volume,
      "maximal_volume": self.maximal_volume,
      "fitting_depth": self.fitting_depth,
    }

  def __hash__(self):
    return hash(
      (
        self.has_filter,
        self.total_tip_length,
        self.nominal_volume,
        self.maximal_volume,
        self.fitting_depth,
      )
    )

  def __eq__(self, other: object) -> bool:
    if not isinstance(other, Tip):
      return NotImplemented

    return (
      self.has_filter == other.has_filter
      and self.total_tip_length == other.total_tip_length
      and self.nominal_volume == other.nominal_volume
      and self.maximal_volume == other.maximal_volume
      and self.fitting_depth == other.fitting_depth
    )


TipCreator = Callable[[str], Tip]
