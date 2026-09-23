import unittest

from pylabrobot.byonoy import byonoy_a96a_illumination_unit, byonoy_a96a_parking_unit
from pylabrobot.resources import Coordinate, Lid, Plate, Well


def _plate(size_z: float) -> Plate:
  well = Well(name="well", size_x=8, size_y=8, size_z=size_z - 1)
  well.location = Coordinate(x=10, y=70, z=1)
  return Plate(name="plate", size_x=127.76, size_y=85.48, size_z=size_z, ordered_items={"A1": well})


class IlluminationUnitHeightCheckTests(unittest.TestCase):
  def _check_with(self, plate: Plate) -> None:
    base = byonoy_a96a_parking_unit(name="base")
    base.plate_holder.assign_child_resource(plate)
    base.illumination_unit_holder.check_can_drop_resource_here(
      byonoy_a96a_illumination_unit(name="illumination_unit")
    )

  def test_the_tallest_plate_that_clears_is_allowed_and_the_next_is_not(self):
    self._check_with(_plate(16.0))
    with self.assertRaises(RuntimeError):
      self._check_with(_plate(16.01))

  def test_a_lid_counts_towards_the_height(self):
    plate = _plate(14.0)
    plate.assign_child_resource(
      Lid(name="lid", size_x=127.76, size_y=85.48, size_z=4.0, nesting_z_height=1.0)
    )
    with self.assertRaises(RuntimeError):
      self._check_with(plate)
