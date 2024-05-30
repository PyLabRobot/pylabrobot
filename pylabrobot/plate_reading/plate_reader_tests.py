""" Tests for plate reader """

import unittest

from pylabrobot.plate_reading import PlateReader
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate


class MockPlateReaderBackend(PlateReaderBackend):
  """ A mock backend for testing. """

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def open(self):
    pass

  async def close(self):
    pass

  async def read_luminescence(self):
    return [[1, 2, 3], [4, 5, 6]]

  async def read_absorbance(self):
    return [[1, 2, 3], [4, 5, 6]]


class TestPlateReaderResource(unittest.TestCase):
  """ Test plate reade as a resource. """

  def setUp(self) -> None:
    super().setUp()
    self.pr = PlateReader(name="pr", backend=MockPlateReaderBackend(), size_x=1, size_y=1, size_z=1)

  def test_add_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=1,
      items=[])
    self.pr.assign_child_resource(plate)

  def test_add_plate_full(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=1,
      items=[])
    self.pr.assign_child_resource(plate)

    another_plate = Plate("another_plate", size_x=1, size_y=1, size_z=1, lid_height=1, items=[])
    with self.assertRaises(ValueError):
      self.pr.assign_child_resource(another_plate)

  def test_get_plate(self):
    plate = Plate("plate", size_x=1, size_y=1, size_z=1, lid_height=1, items=[])
    self.pr.assign_child_resource(plate)

    self.assertEqual(self.pr.get_plate(), plate)

  def test_serialization(self):
    backend = MockPlateReaderBackend()
    self.assertEqual(backend.serialize(), {
      "type": "MockPlateReaderBackend",
    })
    self.assertIsInstance(backend.deserialize(backend.serialize()), MockPlateReaderBackend)
