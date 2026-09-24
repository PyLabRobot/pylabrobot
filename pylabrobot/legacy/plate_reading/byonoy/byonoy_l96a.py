from typing import Tuple

from pylabrobot.resources import Coordinate

from .byonoy_l96 import (
  ByonoyLuminescence96Automate,
  ByonoyLuminescenceBaseUnit,
)


def byonoy_l96a_reader_unit(name: str) -> ByonoyLuminescence96Automate:
  """Create a Byonoy L96A reader unit `PlateReader`."""
  return ByonoyLuminescence96Automate(
    name=name,
    size_x=138,  # caliper
    size_y=97.5,  # caliper
    size_z=41.7,  # force z probing
    preferred_pickup_location=Coordinate(x=69, y=48.75, z=33.2),  # z = 41.7 - 8.5
  )


def byonoy_l96a_base_unit(name: str) -> ByonoyLuminescenceBaseUnit:
  """Create a Byonoy L96A base unit."""
  return ByonoyLuminescenceBaseUnit(
    name=name,
    size_x=138,  # caliper
    size_y=97.5,  # caliper
    size_z=10.7,  # force z probing
    plate_holder_child_location=Coordinate(x=5.1, y=4.75, z=8),  # caliper
    reader_unit_holder_child_location=Coordinate(x=0, y=0, z=6.3),  # z = 48 - 41.7
  )


def byonoy_l96a(
  name: str, assign: bool = True
) -> Tuple[ByonoyLuminescenceBaseUnit, ByonoyLuminescence96Automate]:
  """Creates a ByonoyLuminescenceBaseUnit and a PlateReader instance for L96A (automate).

  Args:
    name: Base name for the resources.
    assign: If True, the reader unit is assigned to the base unit's reader_unit_holder.

  Returns:
    A tuple of (base_unit, reader_unit).
  """
  base_unit = byonoy_l96a_base_unit(name=name + "_base")
  reader_unit = byonoy_l96a_reader_unit(name=name + "_reader")
  if assign:
    base_unit.reader_unit_holder.assign_child_resource(reader_unit)
  return base_unit, reader_unit
