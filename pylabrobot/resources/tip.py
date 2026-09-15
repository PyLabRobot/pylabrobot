from __future__ import annotations

import warnings
from typing import Callable, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.volume_tracker import VolumeTracker


class Tip(HeadTool):
  """A single tip.

  A tip is a head tool whose working point is its end, so its `total_length` is the tip's own
  length, and so is its `size_z`.

  Attributes:
    has_filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in mm
    nominal_volume: rated working volume of the tip (what it is sold and named as), in uL.
      Defaults to maximal_volume when not given.
    maximal_volume: physical brim-full capacity of the tip, in uL
    fitting_depth: the overlap between the tip and the pipette, in mm
    collar_height: the height of the collar, in mm
  """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    fitting_depth: float,
    nominal_volume: Optional[float] = None,
    name: Optional[str] = None,
    collar_height: Optional[float] = None,
    size_x: float = 0,
    size_y: float = 0,
    size_z: Optional[float] = None,
    category: str = "tip",
    model: Optional[str] = None,
    pick_up_location: Optional[Coordinate] = None,
  ):
    """Initialize a tip.

    Args:
      size_z: accepted so that a serialized tip deserializes. A tip's `size_z` is its length, so
        this must equal `total_tip_length` when given.
    """

    if size_z is not None and size_z != total_tip_length:
      raise ValueError(
        f"size_z ({size_z}) of a tip is its length and must equal total_tip_length "
        f"({total_tip_length})."
      )

    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=total_tip_length,
      fitting_depth=fitting_depth,
      collar_height=collar_height,
      category=category,
      model=model,
      pick_up_location=pick_up_location,
    )
    self.has_filter = has_filter
    self.total_tip_length = total_tip_length
    self.maximal_volume = maximal_volume
    self.nominal_volume = maximal_volume if nominal_volume is None else nominal_volume

    if name is None:
      warnings.warn(
        "Creating a Tip without a name is deprecated. "
        "Tips created from deck resources (e.g. TipSpot) should be named.",
        DeprecationWarning,
        stacklevel=2,
      )

    self.tracker = VolumeTracker(thing=name or "tip_tracker", max_volume=self.maximal_volume)

  @property
  def total_length(self) -> float:
    return self.total_tip_length

  def definition(self) -> Tuple[object, ...]:
    return (
      self.has_filter,
      self.total_tip_length,
      self.nominal_volume,
      self.maximal_volume,
      self.fitting_depth,
      self._collar_height,
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "total_tip_length": self.total_tip_length,
      "has_filter": self.has_filter,
      "nominal_volume": self.nominal_volume,
      "maximal_volume": self.maximal_volume,
    }


TipCreator = Callable[[str], Tip]
