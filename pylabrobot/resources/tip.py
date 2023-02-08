from __future__ import annotations

import sys
from typing import Callable

from pylabrobot.resources.volume_tracker import TipVolumeTracker

if sys.version_info >= (3, 11):
  from typing import Self
else:
  from typing_extensions import Self


class Tip:
  """ A single tip.

  Attributes:
    has_filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in in mm
    maximal_volume: maximal volume of the tip, in ul
    fitting_depth: the overlap between the tip and the pipette, in mm
  """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    fitting_depth: float
  ):
    self.has_filter = has_filter
    self.total_tip_length = total_tip_length
    self.maximal_volume = maximal_volume
    self.fitting_depth = fitting_depth

    self.tracker = TipVolumeTracker(max_volume=self.maximal_volume)

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, Tip) and
      self.has_filter == other.has_filter and
      self.total_tip_length == other.total_tip_length and
      self.maximal_volume == other.maximal_volume and
      self.fitting_depth == other.fitting_depth
    )

  def __hash__(self) -> int:
    return hash(self.__repr__())

  def serialize(self) -> dict:
    return {
      "type": self.__class__.__name__,
      "total_tip_length": self.total_tip_length,
      "has_filter": self.has_filter,
      "maximal_volume": self.maximal_volume,
      "fitting_depth": self.fitting_depth,
    }

  @classmethod
  def deserialize(cls, data: dict) -> Self: # type: ignore
    tip_class_name = data.pop("type")
    tip_classes = {cls.__name__: cls for cls in cls.__subclasses__()}
    tip_classes["Tip"] = cls
    tip_class = tip_classes[tip_class_name]
    return tip_class(**data)


TipCreator = Callable[[], Tip]
