""" Tests for Carrier resource """
# pylint: disable=missing-class-docstring

import unittest

from .carrier import Carrier, TipCarrier, create_homogeneous_carrier_sites
from .coordinate import Coordinate
from .deck import Deck
from .resource import Resource
from .tip_rack import TipRack


class CarrierTests(unittest.TestCase):
  def setUp(self):
    # pylint: disable=invalid-name
    self.A = TipRack(name="A", size_x=5, size_y=5, size_z=5, items=[])
    self.B = TipRack(name="B", size_x=5, size_y=5, size_z=5, items=[])
    self.alsoB = TipRack(name="B", size_x=100, size_y=100, size_z=100, items=[])

    self.tip_car = TipCarrier(
      "tip_car",
      size_x=135.0, size_y=497.0, size_z=13.0,
      sites=create_homogeneous_carrier_sites([
          Coordinate(10,   20, 30),
          Coordinate(10,   50, 30),
          Coordinate(10,   80, 30),
          Coordinate(10,  130, 30),
          Coordinate(10,  160, 30),
        ],
        site_size_x=10, site_size_y=10)
    )

  def test_assign_in_order(self):
    carrier = Carrier(
      name="carrier",
      size_x=200, size_y=200, size_z=50,
      sites=create_homogeneous_carrier_sites([Coordinate(5, 5, 5)], site_size_x=10, site_size_y=10)
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)

    self.assertEqual(carrier.get_resource("plate"), plate)
    assert plate.parent is not None
    self.assertEqual(plate.parent, carrier[0])
    self.assertEqual(plate.parent.parent, carrier)
    self.assertEqual(carrier.parent, None)

  def test_assign_build_carrier_first(self):
    carrier = Carrier(
      name="carrier",
      size_x=200, size_y=200, size_z=50,
      sites=create_homogeneous_carrier_sites([Coordinate(5, 5, 5)], site_size_x=10, site_size_y=10)
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)

    deck = Deck()
    deck.assign_child_resource(carrier, location=Coordinate.zero())

    self.assertEqual(deck.get_resource("carrier"), carrier)
    self.assertEqual(deck.get_resource("plate"), plate)
    self.assertEqual(plate.parent, carrier[0])

  def test_unassign_child(self):
    carrier = Carrier(
      name="carrier",
      size_x=200, size_y=200, size_z=50,
      sites=create_homogeneous_carrier_sites([Coordinate(5, 5, 5)], site_size_x=10, site_size_y=10)
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)
    carrier.unassign_child_resource(plate)
    deck = Deck()
    deck.assign_child_resource(carrier, location=Coordinate.zero())

    self.assertIsNone(plate.parent)
    with self.assertRaises(ValueError):
      carrier.get_resource("plate")
    with self.assertRaises(ValueError):
      deck.get_resource("plate")

  def test_assign_index_error(self):
    carrier = Carrier(
      name="carrier",
      size_x=200, size_y=200, size_z=50,
      sites=create_homogeneous_carrier_sites([Coordinate(5, 5, 5)], site_size_x=10, site_size_y=10)
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    with self.assertRaises(IndexError):
      carrier.assign_resource_to_site(plate, spot=3)

  def test_absolute_location(self):
    carrier = Carrier(
      name="carrier",
      size_x=200, size_y=200, size_z=50,
      sites=create_homogeneous_carrier_sites([Coordinate(5, 5, 5)], site_size_x=10, site_size_y=10)
    )
    carrier.location = Coordinate(10, 10, 10)
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)

    self.assertEqual(carrier.get_resource("plate").get_absolute_location(), Coordinate(15, 15, 15))

  def test_capacity(self):
    self.assertEqual(self.tip_car.capacity, 5)

  def test_assignment(self):
    self.tip_car[0] = self.A
    self.tip_car[1] = self.B

  def test_get(self):
    self.tip_car[0] = self.A
    self.tip_car[1] = self.B

    assert self.tip_car[0].resource is not None
    self.assertEqual(self.tip_car[0].resource.name, "A")
    assert self.tip_car[1].resource is not None
    self.assertEqual(self.tip_car[1].resource.name, "B")
    self.assertIsNone(self.tip_car[2].resource)
    self.assertIsNone(self.tip_car[3].resource)
    self.assertIsNone(self.tip_car[4].resource)

  def test_unassign_carrier_site(self):
    pass

  def test_assign_to_carrier_site(self):
    pass

  # few tests for __getitem__ and __setitem__

  def test_illegal_assignment(self):
    with self.assertRaises(IndexError):
      self.tip_car[-1] = self.A
    with self.assertRaises(IndexError):
      self.tip_car[99999] = self.A

  def test_illegal_get(self):
    with self.assertRaises(IndexError):
      self.tip_car[-1] # pylint: disable=pointless-statement
    with self.assertRaises(IndexError):
      self.tip_car[99999] # pylint: disable=pointless-statement

  def test_nonnone_to_none_assignment(self):
    self.tip_car[0] = self.A
    self.tip_car[0] = None
    self.assertIsNone(self.tip_car[0].resource)

  def test_none_to_none_assignment(self):
    self.tip_car[0] = None
    self.assertIsNone(self.tip_car[0].resource)

  def test_over_assignment(self):
    self.tip_car[0] = self.A
    with self.assertRaises(ValueError):
      self.tip_car[0] = self.B

  def test_serialization(self):
    self.maxDiff = None # pylint: disable=invalid-name
    self.assertEqual(self.tip_car.serialize(), {
      "name": "tip_car",
      "type": "TipCarrier",
      "size_x": 135.0,
      "size_y": 497.0,
      "size_z": 13.0,
      "location": None,
      "category": "tip_carrier",
      "model": None,
      "parent_name": None,
      "children": [
        {
          "spot": 0,
          "name": "carrier-tip_car-spot-0",
          "type": "CarrierSite",
          "size_x": 10,
          "size_y": 10,
          "size_z": 0,
          "location": {
            "type": "Coordinate",
            "x": 10,
            "y": 20,
            "z": 30
          },
          "category": "carrier_site",
          "children": [],
          "parent_name": "tip_car",
          "model": None
        },
        {
          "spot": 1,
          "name": "carrier-tip_car-spot-1",
          "type": "CarrierSite",
          "size_x": 10,
          "size_y": 10,
          "size_z": 0,
          "location": {
            "type": "Coordinate",
            "x": 10,
            "y": 50,
            "z": 30
          },
          "category": "carrier_site",
          "children": [],
          "parent_name": "tip_car",
          "model": None
        },
        {
          "spot": 2,
          "name": "carrier-tip_car-spot-2",
          "type": "CarrierSite",
          "size_x": 10,
          "size_y": 10,
          "size_z": 0,
          "location": {
            "type": "Coordinate",
            "x": 10,
            "y": 80,
            "z": 30
          },
          "category": "carrier_site",
          "children": [],
          "parent_name": "tip_car",
          "model": None
        },
        {
          "spot": 3,
          "name": "carrier-tip_car-spot-3",
          "type": "CarrierSite",
          "size_x": 10,
          "size_y": 10,
          "size_z": 0,
          "location": {
            "type": "Coordinate",
            "x": 10,
            "y": 130,
            "z": 30
          },
          "category": "carrier_site",
          "children": [],
          "parent_name": "tip_car",
          "model": None
        },
        {
          "spot": 4,
          "name": "carrier-tip_car-spot-4",
          "type": "CarrierSite",
          "size_x": 10,
          "size_y": 10,
          "size_z": 0,
          "location": {
            "type": "Coordinate",
            "x": 10,
            "y": 160,
            "z": 30
          },
          "category": "carrier_site",
          "children": [],
          "parent_name": "tip_car",
          "model": None
        }
      ]
    })

  def test_deserialization(self):
    self.maxDiff = None
    # sites are not deserialized here)
    tip_car = TipCarrier(
      "tip_car",
      size_x=135.0, size_y=497.0, size_z=13.0,
      sites=[]
    )
    self.assertEqual(tip_car, TipCarrier.deserialize(tip_car.serialize()))
