"""Legacy. Use pylabrobot.byonoy instead."""

from typing import Tuple

from pylabrobot.byonoy.luminescence_96 import ByonoyLuminescenceBaseUnit
from pylabrobot.resources import Coordinate

from .byonoy_l96 import ByonoyLuminescence96Automate


def byonoy_l96a_reader_unit(name: str) -> ByonoyLuminescence96Automate:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96a_reader_unit instead."""
  return ByonoyLuminescence96Automate(
    name=name,
    size_x=138,
    size_y=97.5,
    size_z=41.7,
    preferred_pickup_location=Coordinate(x=69, y=48.75, z=33.2),
  )


def byonoy_l96a_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96a_base_unit instead."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=138,
    size_y=97.5,
    size_z=10.7,
    plate_holder_child_location=Coordinate(x=5.1, y=4.75, z=8),
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=6.3),
  )


def byonoy_l96a(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96Automate]:
  """Legacy. Use pylabrobot.byonoy.byonoy_l96a instead."""
  base_unit = byonoy_l96a_base_unit(name=name + "_base")
  reader_unit = byonoy_l96a_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit
