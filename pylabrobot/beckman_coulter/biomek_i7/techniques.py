"""The arguments the pipetting features take that PyLabRobot has no type for, named after what
Biomek's techniques hold: conditioning, a tip touch, a mix's heights, and what a height is
measured from."""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class Conditioning:
  """Biomek's conditioning: aspirate `sections` x `section_volume` + `excess` uL more than asked,
  then dispense each section back in place before leaving the well (`MC Pipetting` / `S8
  Pipetting`'s loop), leaving the excess in the tip. The sections go back at `flow_rate` uL/s (the
  technique's dispense speed; the aspirate's when None), each followed by `delay` s."""

  sections: int = 0
  section_volume: float = 0.0
  excess: float = 0.0
  flow_rate: Optional[float] = None
  delay: float = 0.0

  @property
  def extra(self) -> float:
    return self.sections * self.section_volume + self.excess


HeightFrom = Literal["bottom", "top", "liquid"]
"""What a height is measured from: the container's bottom, its top, or the liquid in it."""


@dataclass(frozen=True)
class TipTouch:
  """A touch-off: the tip end to `height` mm from `height_from`, out `distance` mm in the direction
  `theta` (degrees counterclockwise from +X seen from above, PyLabRobot's: 90 is towards the back of
  the deck; Biomek states the same figure, and turns by 360 less it) and back, at `speed_fraction` of
  the axes' maximum ((0, 1]: the touch moves X and Y together), then `delay` s. `distance` None goes to the container's wall at its top outline, less the
  tip's widest radius (STAR's `side_touch_off_distance` is the same kind of figure). Biomek goes to
  the wall at the touch height less its tip's cone where it crosses the rim:
  `resource_model.labware.biomek_touch_distance` gives that distance for its labware."""

  height: float
  height_from: HeightFrom = "liquid"
  theta: float = 90.0
  speed_fraction: float = 1.0
  delay: float = 0.0
  distance: Optional[float] = None


@dataclass(frozen=True)
class MixHeights:
  """Where a mix's tips stand: each draw `aspirate_height` mm from `aspirate_height_from`, each push
  `dispense_height` mm from `dispense_height_from` (None: as the draw's). Biomek's Mix tab of a
  technique section (Aspirate / Dispense From and Height). A mix measured from the liquid needs the
  container's contents, as any height from the liquid does; STAR's
  `mix_position_from_liquid_surface` d is `MixHeights(-d, "liquid")`."""

  aspirate_height: float
  aspirate_height_from: HeightFrom = "bottom"
  dispense_height: Optional[float] = None
  dispense_height_from: Optional[HeightFrom] = None
