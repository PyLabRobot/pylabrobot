""" Tests for Carrier resource """
# pylint: disable=missing-class-docstring

import unittest

from .tips import Tips
from .tip_type import TipType, TIP_TYPE_STANDARD_VOLUME
from .carrier import TipCarrier, CarrierSite
from .coordinate import Coordinate


class TestLiquidHandlerLayout(unittest.TestCase):
  def setUp(self):
    tip_type = TipType(
      has_filter=False,
      total_tip_length=100,
      maximal_volume=400,
      tip_type_id=TIP_TYPE_STANDARD_VOLUME,
      pick_up_method=0
    )

    self.A = Tips( # pylint: disable=invalid-name
      name="A",
      size_x=5, size_y=5, size_z=5,
      tip_type=tip_type,
      dx=1, dy=1, dz=1
    )

    self.B = Tips( # pylint: disable=invalid-name
      name="B",
      size_x=5, size_y=5, size_z=5,
      tip_type=tip_type,
      dx=9, dy=2, dz=-2
    )

    self.alsoB = Tips( # pylint: disable=invalid-name
      name="B",
      size_x=100, size_y=100, size_z=100,
      tip_type=tip_type,
      dx=0, dy=0, dz=0
    )

    self.tip_car = TipCarrier(
      "tip_car",
      size_x=135.0, size_y=497.0, size_z=13.0,
      sites=[
        CarrierSite(Coordinate(10,   20, 30), 10, 10),
        CarrierSite(Coordinate(10,   50, 30), 10, 10),
        CarrierSite(Coordinate(10,   80, 30), 10, 10),
        CarrierSite(Coordinate(10,  130, 30), 10, 10),
        CarrierSite(Coordinate(10,  160, 30), 10, 10),
      ])

  def test_capacity(self):
    self.assertEqual(self.tip_car.capacity, 5)

  def test_assignment(self):
    self.tip_car[0] = self.A
    self.tip_car[1] = self.B

  def test_get(self):
    self.tip_car[0] = self.A
    self.tip_car[1] = self.B

    self.assertEqual(self.tip_car[0].name, "A")
    self.assertEqual(self.tip_car[1].name, "B")
    self.assertIsNone(self.tip_car[2])
    self.assertIsNone(self.tip_car[3])
    self.assertIsNone(self.tip_car[4])

  def test_illegal_assignment(self):
    with self.assertRaises(KeyError):
      self.tip_car[-1] = self.A
    with self.assertRaises(KeyError):
      self.tip_car[99999] = self.A

    self.tip_car[0] = self.B
    with self.assertRaises(ValueError):
      self.tip_car[1] = self.alsoB

  def test_illegal_get(self):
    with self.assertRaises(KeyError):
      self.tip_car[-1] # pylint: disable=pointless-statement
    with self.assertRaises(KeyError):
      self.tip_car[99999] # pylint: disable=pointless-statement

  def test_over_assignment(self):
    with self.assertLogs() as captured:
      self.tip_car[0] = self.A
      self.tip_car[0] = self.B
    self.assertIn("Overriding", captured.records[0].getMessage())

  def test_location(self):
    self.tip_car[0] = self.A
    self.assertEqual(self.tip_car[0].location, Coordinate(11, 21, 31))

    self.tip_car[1] = self.B
    self.assertEqual(self.tip_car[1].location, Coordinate(19, 52, 28))

  def test_serialization(self):
    self.maxDiff = None # pylint: disable=invalid-name
    self.assertEqual(self.tip_car.serialize(), {
      "category": "tip_carrier",
      "location": {"x": None, "y": None, "z": None},
      "name": "tip_car",
      "sites": [
        {
          "site": {
            "location": {"x": 10, "y": 20, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10
          },
          "site_id": 0
        },
        {
          "site": {
            "location": {"x": 10, "y": 50, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10
          },
          "site_id": 1
        },
        {
          "site": {
            "location": {"x": 10, "y": 80, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10
          },
          "site_id": 2
        },
        {
          "site": {
            "location": {"x": 10, "y": 130, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10
          },
          "site_id": 3
        },
        {
          "site": {
            "location": {"x": 10, "y": 160, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10
          },
          "site_id": 4
        }
      ],
      "size_x": 135.0,
      "size_y": 497.0,
      "size_z": 13.0,
      "type": "TipCarrier"
    })

    self.tip_car[1] = self.B
    self.assertEqual(self.tip_car.serialize(), {
      "category": "tip_carrier",
      "location": {"x": None, "y": None, "z": None},
      "name": "tip_car",
      "sites": [
        {
          "site": {
            "location": {"x": 10, "y": 20, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10,
          },
          "site_id": 0
        },
        {
          "site": {
            "location": {"x": 10, "y": 50, "z": 30},
            "resource": {
              "category": "tips",
              "dx": 9,
              "dy": 2,
              "dz": -2,
              "location": {"x": 19, "y": 52, "z": 28},
              "name": "B",
              "size_x": 5,
              "size_y": 5,
              "size_z": 5,
              "tip_type": {
                "has_filter": False,
                "maximal_volume": 400,
                "pick_up_method": 0,
                "tip_type_id": 2,
                "total_tip_length": 100
              },
              "type": "Tips"
            },
            "width": 10,
            "height": 10
          },
          "site_id": 1
        },
        {
          "site": {
            "location": {"x": 10, "y": 80, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10,
          },
          "site_id": 2
        },
        {
          "site": {
            "location": {"x": 10, "y": 130, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10,
          },
          "site_id": 3
        },
        {
          "site": {
            "location": {"x": 10, "y": 160, "z": 30},
            "resource": None,
            "width": 10,
            "height": 10,
          },
          "site_id": 4
        }
      ],
      "size_x": 135.0,
      "size_y": 497.0,
      "size_z": 13.0,
      "type": "TipCarrier"
    })


if __name__ == "__main__":
  unittest.main()
