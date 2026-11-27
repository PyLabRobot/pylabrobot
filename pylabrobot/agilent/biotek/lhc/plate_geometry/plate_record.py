"""What the instrument knows about a plate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType

Head = Literal["dispenser", "manifold dispense", "manifold aspirate"]
"""Which head a nominal height belongs to.

A plate is worked from a different height by each of them, so asking for a height means saying
which one is doing the work.
"""


@dataclass(frozen=True)
class PlateRecord:
  """The geometry an instrument works a plate with.

  The three heights are in motor steps and are the nominal positions a step's own offsets are
  measured from: a dispenser measures from :attr:`dispenser_height`, a wash manifold from
  :attr:`manifold_dispense_height` when dispensing and :attr:`manifold_aspirate_height` when
  aspirating.

  Attributes:
    plate_type: Which plate this is.
    label: The plate's name.
    dispenser_height: Nominal dispensing height for a dispenser.
    manifold_dispense_height: Nominal dispensing height for a wash manifold.
    manifold_aspirate_height: Nominal aspirating height for a wash manifold.
    columns: How many columns the plate has.
    rows: How many rows the plate has.
  """

  plate_type: PlateType
  label: str
  dispenser_height: int
  manifold_dispense_height: int
  manifold_aspirate_height: int
  columns: int
  rows: int

  @property
  def wells(self) -> int:
    """How many wells the plate has."""
    return self.columns * self.rows

  def height_for(self, head: Head) -> int:
    """The nominal height this plate is worked from by one head.

    A step carries the height it works at outright rather than an offset from anything, so this is
    what a step should be given when the caller does not name a height of its own. Getting it from
    the plate is what stops a height that suits one plate being used on a shallower one.

    Args:
      head: Which head is doing the work.

    Returns:
      The height, in motor steps.
    """
    heights: dict[Head, int] = {
      "dispenser": self.dispenser_height,
      "manifold dispense": self.manifold_dispense_height,
      "manifold aspirate": self.manifold_aspirate_height,
    }
    return heights[head]
