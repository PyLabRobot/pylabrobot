"""Legacy. Use pylabrobot.byonoy instead."""

from typing import Optional, Tuple

from pylabrobot.byonoy.luminescence_96 import ByonoyLuminescenceBaseUnit
from pylabrobot.legacy.plate_reading.byonoy.byonoy_backend import ByonoyLuminescence96AutomateBackend
from pylabrobot.legacy.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Coordinate, Resource


class ByonoyLuminescence96Automate(PlateReader):
  """Legacy. Use pylabrobot.byonoy.ByonoyLuminescence96 instead."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    preferred_pickup_location: Optional[Coordinate] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      backend=ByonoyLuminescence96AutomateBackend(),
      model="Byonoy L96 Reader Unit",
      preferred_pickup_location=preferred_pickup_location,
    )


def byonoy_l96_reader_unit(name: str) -> ByonoyLuminescence96Automate:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96_reader_unit instead."""
  return ByonoyLuminescence96Automate(
    name=name,
    size_x=139.7,
    size_y=97.5,
    size_z=35,
    preferred_pickup_location=None,
  )


def byonoy_l96_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96_base_unit instead."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=139.7,
    size_y=97.5,
    size_z=9.4,
    plate_holder_child_location=Coordinate(x=6.25, y=6.1, z=2.64),
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=7.2),
  )


def byonoy_l96(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96Automate]:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96 instead."""
  base_unit = byonoy_l96_base_unit(name=name + "_base")
  reader_unit = byonoy_l96_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit
