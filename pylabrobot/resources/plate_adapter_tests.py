import unittest

from pylabrobot.resources import (
  Coordinate,
  Plate,
  PlateAdapter,
  Well,
  create_ordered_items_2d,
)

_adapter = PlateAdapter(
  name="adapter",
  size_x=128.0,
  size_y=86.0,
  size_z=15.0,
  dx=0.0,
  dy=1.0,
  dz=2.0,
  adapter_hole_size_x=100.0,
  adapter_hole_size_y=80.0,
  adapter_hole_dx=9.0,
  adapter_hole_dy=9.0,
  plate_z_offset=3.0,
)


class TestPlateAdapter(unittest.TestCase):
  def test_plate_adapter_serialization(self):
    serialized = _adapter.serialize()
    self.assertEqual(
      serialized,
      {
        "name": "adapter",
        "type": "PlateAdapter",
        "size_x": 128.0,
        "size_y": 86.0,
        "size_z": 15.0,
        "category": "plate_adapter",
        "dx": 0.0,
        "dy": 1.0,
        "dz": 2.0,
        "adapter_hole_size_x": 100.0,
        "adapter_hole_size_y": 80.0,
        "adapter_hole_dx": 9.0,
        "adapter_hole_dy": 9.0,
        "plate_z_offset": 3.0,
      },
    )

  def test_plate_adapter_deserialization(self):
    assert _adapter == PlateAdapter.deserialize(_adapter.serialize())


class TestComputePlateLocation(unittest.TestCase):
  def test_rectangular_hole_centers_each_axis_on_its_own_size(self):
    """A hole whose x and y sizes differ centers the well on the hole in both directions."""

    adapter = PlateAdapter(
      name="rectangular_hole_adapter",
      size_x=128.0,
      size_y=86.0,
      size_z=15.0,
      dx=10.0,
      dy=8.0,
      dz=2.0,
      adapter_hole_size_x=8.0,
      adapter_hole_size_y=6.0,
      adapter_hole_dx=9.0,
      adapter_hole_dy=9.0,
    )
    adapter.location = Coordinate.zero()
    plate = Plate(
      name="plate",
      size_x=127.76,
      size_y=85.48,
      size_z=14.0,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=9.5,
        dy=6.5,
        dz=1.0,
        item_dx=9.0,
        item_dy=9.0,
        size_x=6.0,
        size_y=6.0,
        size_z=10.0,
      ),
    )

    adapter.assign_child_resource(plate)

    well_center = plate.get_well("H1").get_location_wrt(adapter, x="c", y="c", z="b")
    self.assertAlmostEqual(well_center.x, adapter.dx + adapter.adapter_hole_size_x / 2)
    self.assertAlmostEqual(well_center.y, adapter.dy + adapter.adapter_hole_size_y / 2)
