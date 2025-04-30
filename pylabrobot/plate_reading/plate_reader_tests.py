import unittest

from pylabrobot.plate_reading import PlateReader
from pylabrobot.plate_reading.chatterbox import PlateReaderChatterboxBackend
from pylabrobot.resources import Plate


class TestPlateReaderResource(unittest.TestCase):
  """Test plate reader as a resource."""

  def setUp(self) -> None:
    super().setUp()
    self.pr = PlateReader(
      name="pr",
      backend=PlateReaderChatterboxBackend(),
      size_x=1,
      size_y=1,
      size_z=1,
    )

  def test_add_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

  def test_add_plate_full(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

    another_plate = Plate("another_plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    with self.assertRaises(ValueError):
      self.pr.assign_child_resource(another_plate)

  def test_get_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    self.pr.assign_child_resource(plate)

    self.assertEqual(self.pr.get_plate(), plate)
