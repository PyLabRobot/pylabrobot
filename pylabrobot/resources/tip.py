from __future__ import annotations

import warnings
from typing import Callable, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.head_tool import HeadTool
from pylabrobot.resources.volume_tracker import VolumeTracker
from pylabrobot.resources.well import CrossSectionType


class Tip(HeadTool):
  """A single tip.

  A tip's `size_z` is its total length.

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
    cross_section_type: str = CrossSectionType.CIRCLE.value,
  ):
    """Initialize a tip.

    Args:
      size_z: accepted so that a serialized tip deserializes. A tip's `size_z` is its length, so
        this must equal `total_tip_length` when given.
      cross_section_type: its shape across, as `serialize` writes it. Always a circle; taken so a
        serialized tip deserializes.

    Raises:
      ValueError: If `size_z` is not the tip's length, or the cross section is not a circle.
    """

    if cross_section_type != CrossSectionType.CIRCLE.value:
      raise ValueError(f"a tip is round, not {cross_section_type!r}")

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

  def serialize(self) -> dict:
    """What its size does not say: what it holds, and that it is round rather than a box."""
    return {
      **super().serialize(),
      "total_tip_length": self.total_tip_length,
      "has_filter": self.has_filter,
      "nominal_volume": self.nominal_volume,
      "maximal_volume": self.maximal_volume,
      "cross_section_type": CrossSectionType.CIRCLE.value,
    }

  # A tip compares by what it is, not by which one it is, as it did before it was a resource.
  # `kind()` answers the same question for every head tool; this stays until the two are settled.

  def __hash__(self):
    return hash(
      (
        self.has_filter,
        self.total_tip_length,
        self.nominal_volume,
        self.maximal_volume,
        self.fitting_depth,
        self._collar_height,
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
      and self._collar_height == other._collar_height
    )


TipCreator = Callable[[str], Tip]
