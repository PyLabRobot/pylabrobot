import unittest

from pylabrobot.resources.hamilton.mfx_carriers import hamilton_mfx_carrier_L5_base
from pylabrobot.resources.hamilton.mfx_modules import MFX_TIP_module

from .carrier import (
  Carrier,
  PlateCarrier,
  PlateHolder,
  ResourceHolder,
  TipCarrier,
  create_homogeneous_resources,
)
from .coordinate import Coordinate
from .deck import Deck
from .errors import ResourceNotFoundError
from .plate import Plate
from .resource import Resource
from .resource_stack import ResourceStack
from .tip_rack import TipRack
from .utils import create_ordered_items_2d
from .well import Well


def _make_test_deck() -> Deck:
  return Deck(size_x=100, size_y=100, size_z=100)


class CarrierTests(unittest.TestCase):
  def setUp(self):
    self.A = TipRack(name="A", size_x=5, size_y=5, size_z=5, ordered_items={})
    self.B = TipRack(name="B", size_x=5, size_y=5, size_z=5, ordered_items={})
    self.alsoB = TipRack(name="B", size_x=100, size_y=100, size_z=100, ordered_items={})

    self.tip_car = TipCarrier(
      "tip_car",
      size_x=135.0,
      size_y=497.0,
      size_z=13.0,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[
          Coordinate(10, 20, 30),
          Coordinate(10, 50, 30),
          Coordinate(10, 80, 30),
          Coordinate(10, 130, 30),
          Coordinate(10, 160, 30),
        ],
        resource_size_x=10,
        resource_size_y=10,
        name_prefix="tip_car",
      ),
    )

  def test_assign_in_order(self):
    carrier = Carrier(
      name="carrier",
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
      ),
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
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
      ),
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)

    deck = _make_test_deck()
    deck.assign_child_resource(carrier, location=Coordinate.zero())

    self.assertEqual(deck.get_resource("carrier"), carrier)
    self.assertEqual(deck.get_resource("plate"), plate)
    self.assertEqual(plate.parent, carrier[0])

  def test_unassign_child(self):
    carrier = Carrier(
      name="carrier",
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
      ),
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)
    carrier.unassign_child_resource(plate)
    deck = _make_test_deck()
    deck.assign_child_resource(carrier, location=Coordinate.zero())

    self.assertIsNone(plate.parent)
    with self.assertRaises(ResourceNotFoundError):
      carrier.get_resource("plate")
    with self.assertRaises(ResourceNotFoundError):
      deck.get_resource("plate")

  def test_assign_index_error(self):
    carrier = Carrier(
      name="carrier",
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
      ),
    )
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    with self.assertRaises(KeyError):
      carrier.assign_resource_to_site(plate, spot=3)

  def test_absolute_location(self):
    carrier = Carrier(
      name="carrier",
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=ResourceHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
      ),
    )
    carrier.location = Coordinate(10, 10, 10)
    plate = Resource("plate", size_x=10, size_y=10, size_z=10)
    carrier.assign_resource_to_site(plate, spot=0)

    self.assertEqual(
      carrier.get_resource("plate").get_absolute_location(),
      Coordinate(15, 15, 15),
    )

  def test_capacity(self):
    self.assertEqual(self.tip_car.capacity, 5)

  def test_len(self):
    self.assertEqual(len(self.tip_car), 5)

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

  # few tests for __getitem__ and __setitem__

  def test_illegal_assignment(self):
    with self.assertRaises(KeyError):
      self.tip_car[-1] = self.A
    with self.assertRaises(KeyError):
      self.tip_car[99999] = self.A

  def test_illegal_get(self):
    with self.assertRaises(KeyError):
      self.tip_car[-1]
    with self.assertRaises(KeyError):
      self.tip_car[99999]

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
    self.maxDiff = None
    self.assertEqual(
      self.tip_car.serialize(),
      {
        "name": "tip_car",
        "type": "TipCarrier",
        "size_x": 135.0,
        "size_y": 497.0,
        "size_z": 13.0,
        "location": None,
        "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
        "category": "tip_carrier",
        "model": None,
        "barcode": None,
        "preferred_pickup_location": None,
        "parent_name": None,
        "children": [
          {
            "name": "tip_car-0",
            "type": "ResourceHolder",
            "size_x": 10,
            "size_y": 10,
            "size_z": 0,
            "location": {
              "type": "Coordinate",
              "x": 10,
              "y": 20,
              "z": 30,
            },
            "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
            "category": "resource_holder",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
            "children": [],
            "parent_name": "tip_car",
            "model": None,
            "barcode": None,
            "preferred_pickup_location": None,
          },
          {
            "name": "tip_car-1",
            "type": "ResourceHolder",
            "size_x": 10,
            "size_y": 10,
            "size_z": 0,
            "location": {
              "type": "Coordinate",
              "x": 10,
              "y": 50,
              "z": 30,
            },
            "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
            "category": "resource_holder",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
            "children": [],
            "parent_name": "tip_car",
            "model": None,
            "barcode": None,
            "preferred_pickup_location": None,
          },
          {
            "name": "tip_car-2",
            "type": "ResourceHolder",
            "size_x": 10,
            "size_y": 10,
            "size_z": 0,
            "location": {
              "type": "Coordinate",
              "x": 10,
              "y": 80,
              "z": 30,
            },
            "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
            "category": "resource_holder",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
            "children": [],
            "parent_name": "tip_car",
            "model": None,
            "barcode": None,
            "preferred_pickup_location": None,
          },
          {
            "name": "tip_car-3",
            "type": "ResourceHolder",
            "size_x": 10,
            "size_y": 10,
            "size_z": 0,
            "location": {
              "type": "Coordinate",
              "x": 10,
              "y": 130,
              "z": 30,
            },
            "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
            "category": "resource_holder",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
            "children": [],
            "parent_name": "tip_car",
            "model": None,
            "barcode": None,
            "preferred_pickup_location": None,
          },
          {
            "name": "tip_car-4",
            "type": "ResourceHolder",
            "size_x": 10,
            "size_y": 10,
            "size_z": 0,
            "location": {
              "type": "Coordinate",
              "x": 10,
              "y": 160,
              "z": 30,
            },
            "rotation": {"type": "Rotation", "x": 0, "y": 0, "z": 0},
            "category": "resource_holder",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
            "children": [],
            "parent_name": "tip_car",
            "model": None,
            "barcode": None,
            "preferred_pickup_location": None,
          },
        ],
      },
    )

  def test_deserialization(self):
    self.maxDiff = None
    # sites are not deserialized here)
    tip_car = TipCarrier("tip_car", size_x=135.0, size_y=497.0, size_z=13.0, sites={})
    self.assertEqual(tip_car, TipCarrier.deserialize(tip_car.serialize()))

  def test_assign_resource_stack(self):
    plate1 = Plate(
      name="plate1",
      size_x=10,
      size_y=10,
      size_z=10,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=1,
        num_items_y=1,
        dx=0,
        dy=0,
        dz=5,
        item_dx=10,
        item_dy=10,
        size_x=1,
        size_y=1,
        size_z=1,
      ),
    )
    plate2 = Plate(
      name="plate2",
      size_x=10,
      size_y=10,
      size_z=10,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=1,
        num_items_y=1,
        dx=0,
        dy=0,
        dz=6,
        item_dx=10,
        item_dy=10,
        size_x=1,
        size_y=1,
        size_z=1,
      ),
    )
    carrier = PlateCarrier(
      name="carrier",
      size_x=200,
      size_y=200,
      size_z=50,
      sites=create_homogeneous_resources(
        klass=PlateHolder,
        locations=[Coordinate(5, 5, 5)],
        resource_size_x=10,
        resource_size_y=10,
        pedestal_size_z=10,
      ),
    )
    resource_stack = ResourceStack(name="resource_stack", direction="z", resources=[plate2, plate1])
    carrier[0] = resource_stack
    self.assertEqual(resource_stack.location, Coordinate(0, 0, -5))
    self.assertEqual(plate1.location, Coordinate(0, 0, 0))
    self.assertEqual(plate2.location, Coordinate(0, 0, 10))

    # change the resource stack so that plate2 is on the bottom
    plate2.unassign()
    plate1.unassign()
    resource_stack.assign_child_resource(plate2)
    self.assertEqual(resource_stack.location, Coordinate(0, 0, -6))
    self.assertEqual(plate2.location, Coordinate(0, 0, 0))

    pcs = carrier[0]
    assert isinstance(pcs, PlateHolder)
    self.assertIn(
      pcs._update_resource_stack_location,
      resource_stack._did_assign_resource_callbacks,
    )
    resource_stack.unassign()
    self.assertNotIn(
      pcs._update_resource_stack_location,
      resource_stack._did_assign_resource_callbacks,
    )


class MFXCarrierTests(unittest.TestCase):
  def test_init(self):
    MFX_TIP_module_1 = MFX_TIP_module(name="MFX_TIP_module_1")
    MFX_TIP_module_2 = MFX_TIP_module(name="MFX_TIP_module_2")
    MFX_TIP_module_3 = MFX_TIP_module(name="MFX_TIP_module_3")
    MFX_TIP_module_4 = MFX_TIP_module(name="MFX_TIP_module_4")
    MFX_TIP_module_5 = MFX_TIP_module(name="MFX_TIP_module_5")

    mfx_carrier = hamilton_mfx_carrier_L5_base(
      name="mfx_tip_carrier_1",
      modules={
        4: MFX_TIP_module_5,
        3: MFX_TIP_module_4,
        2: MFX_TIP_module_3,
        1: MFX_TIP_module_2,
        0: MFX_TIP_module_1,
      },
    )
    assert len(mfx_carrier.children) == 5
    assert len(mfx_carrier.sites) == 5
    for i in range(5):
      assert mfx_carrier.sites[i].name == f"MFX_TIP_module_{i + 1}"
