import unittest

from pylabrobot.resources.corning.plates import (
  cor_96_wellplate_360uL_Fb,
  cor_96_wellplate_360uL_Fb_lid,
)
from pylabrobot.resources.hamilton.mfx_carriers import hamilton_mfx_carrier_L5_base
from pylabrobot.resources.hamilton.mfx_modules import (
  hamilton_mfx_tiprackholder_standard,
)
from pylabrobot.resources.hamilton.plate_carriers import hamilton_plate_carrier_L5_ac

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
        "category": "tip_carrier",
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
            "category": "resource_holder",
            "parent_name": "tip_car",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
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
            "category": "resource_holder",
            "parent_name": "tip_car",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
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
            "category": "resource_holder",
            "parent_name": "tip_car",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
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
            "category": "resource_holder",
            "parent_name": "tip_car",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
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
            "category": "resource_holder",
            "parent_name": "tip_car",
            "child_location": {"type": "Coordinate", "x": 0, "y": 0, "z": 0},
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
        name_prefix="plate1",
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
        name_prefix="plate2",
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


class PlateHolderStackedLidTests(unittest.TestCase):
  """Lids on plates that sit in a ResourceStack on a PlateHolder.

  The stack's assignment callback also fires for the stack's descendants, so it is called with the
  lid, whose parent is the plate and not the stack.
  """

  def setUp(self):
    self.carrier = hamilton_plate_carrier_L5_ac(name="carrier")

  def _stack_on_site(self, *plates: Plate, spot: int = 0) -> ResourceStack:
    stack = ResourceStack(name=f"stack_{spot}", direction="z", resources=list(plates))
    self.carrier[spot] = stack
    return stack

  def _reference_lid_z(self) -> float:
    """Absolute z of a lid that was on its plate before the plate was stacked and placed."""
    carrier = hamilton_plate_carrier_L5_ac(name="reference_carrier")
    plate = cor_96_wellplate_360uL_Fb(name="reference_plate", with_lid=True)
    carrier[0] = ResourceStack(name="reference_stack", direction="z", resources=[plate])
    assert plate.lid is not None
    return plate.lid.get_absolute_location().z

  def _assert_lid_in_place(self, plate: Plate, stack: ResourceStack, stack_location):
    self.assertTrue(plate.has_lid())
    assert plate.lid is not None
    self.assertEqual(stack.location, stack_location)
    self.assertAlmostEqual(plate.lid.get_absolute_location().z, self._reference_lid_z())

  def test_lid_assigned_after_stack_is_on_site(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    stack = self._stack_on_site(plate)
    stack_location = stack.location

    plate.assign_child_resource(cor_96_wellplate_360uL_Fb_lid(name="lid"))
    self._assert_lid_in_place(plate, stack, stack_location)

  def test_lid_removed_and_put_back(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate", with_lid=True)
    stack = self._stack_on_site(plate)
    stack_location = stack.location
    lid = plate.lid
    assert lid is not None

    lid.unassign()
    self.assertFalse(plate.has_lid())
    self.assertEqual(stack.location, stack_location)
    plate.assign_child_resource(lid)
    self._assert_lid_in_place(plate, stack, stack_location)

  def test_lid_moved_from_plate_on_another_site(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    stack = self._stack_on_site(plate)
    stack_location = stack.location
    other = cor_96_wellplate_360uL_Fb(name="other", with_lid=True)
    self.carrier[1] = other
    lid = other.lid
    assert lid is not None

    lid.unassign()
    plate.assign_child_resource(lid)
    self.assertFalse(other.has_lid())
    self._assert_lid_in_place(plate, stack, stack_location)

  def test_lid_moved_between_plates_in_two_stacks(self):
    source = cor_96_wellplate_360uL_Fb(name="source", with_lid=True)
    target = cor_96_wellplate_360uL_Fb(name="target")
    source_stack = self._stack_on_site(source, spot=0)
    target_stack = self._stack_on_site(target, spot=1)
    source_location, target_location = source_stack.location, target_stack.location
    lid = source.lid
    assert lid is not None

    lid.unassign()
    target.assign_child_resource(lid)
    self.assertFalse(source.has_lid())
    self.assertEqual(source_stack.location, source_location)
    self._assert_lid_in_place(target, target_stack, target_location)

  def test_lid_on_plate_after_stack_moved_to_another_site(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    stack = self._stack_on_site(plate)
    stack.unassign()
    self.carrier[2] = stack
    stack_location = stack.location

    plate.assign_child_resource(cor_96_wellplate_360uL_Fb_lid(name="lid"))
    self.assertTrue(plate.has_lid())
    self.assertEqual(stack.location, stack_location)

  def test_lid_on_top_plate_of_two(self):
    bottom = cor_96_wellplate_360uL_Fb(name="bottom")
    top = cor_96_wellplate_360uL_Fb(name="top")
    stack = self._stack_on_site(bottom)
    stack.assign_child_resource(top)
    stack_location = stack.location

    top.assign_child_resource(cor_96_wellplate_360uL_Fb_lid(name="lid"))
    self.assertTrue(top.has_lid())
    self.assertFalse(bottom.has_lid())
    self.assertEqual(stack.location, stack_location)

  def test_lid_present_before_stack_is_on_site(self):
    """Lids that are on the plate before the stack is placed never reach the callback."""
    from_factory = cor_96_wellplate_360uL_Fb(name="from_factory", with_lid=True)
    assigned_first = cor_96_wellplate_360uL_Fb(name="assigned_first")
    assigned_first.assign_child_resource(cor_96_wellplate_360uL_Fb_lid(name="lid"))

    for spot, plate in enumerate((from_factory, assigned_first)):
      stack = self._stack_on_site(plate, spot=spot)
      self._assert_lid_in_place(plate, stack, stack.location)

  def test_lidded_plate_added_to_stack_on_site(self):
    bottom = cor_96_wellplate_360uL_Fb(name="bottom")
    stack = self._stack_on_site(bottom)
    stack_location = stack.location

    top = cor_96_wellplate_360uL_Fb(name="top", with_lid=True)
    stack.assign_child_resource(top)
    self.assertTrue(top.has_lid())
    self.assertEqual(stack.location, stack_location)

  def test_lid_assigned_to_the_stack_itself(self):
    """A lid parked on top of the stack is a child of the stack, and not the lowest one."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    stack = self._stack_on_site(plate)
    stack_location = stack.location

    lid = cor_96_wellplate_360uL_Fb_lid(name="lid")
    stack.assign_child_resource(lid)
    self.assertFalse(plate.has_lid())
    self.assertIs(stack.get_top_item(), lid)
    self.assertEqual(stack.location, stack_location)

  def test_lid_removed_and_put_back_after_serialization(self):
    original = cor_96_wellplate_360uL_Fb(name="plate", with_lid=True)
    stack_location = self._stack_on_site(original).location

    carrier = PlateCarrier.deserialize(self.carrier.serialize())
    plate = carrier.get_resource("plate")
    assert isinstance(plate, Plate) and plate.lid is not None
    stack = plate.parent
    assert isinstance(stack, ResourceStack)
    lid = plate.lid
    lid.unassign()
    plate.assign_child_resource(lid)
    self.assertTrue(plate.has_lid())
    self.assertEqual(stack.location, stack_location)

  def test_lid_on_plate_added_to_empty_stack(self):
    """The other assignment order: the stack is placed on the site before it holds a plate."""
    stack = self._stack_on_site()
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    stack.assign_child_resource(plate)

    plate.assign_child_resource(cor_96_wellplate_360uL_Fb_lid(name="lid"))
    self.assertTrue(plate.has_lid())


class MFXCarrierTests(unittest.TestCase):
  def test_init(self):
    MFX_TIP_module_1 = hamilton_mfx_tiprackholder_standard(name="MFX_TIP_module_1")
    MFX_TIP_module_2 = hamilton_mfx_tiprackholder_standard(name="MFX_TIP_module_2")
    MFX_TIP_module_3 = hamilton_mfx_tiprackholder_standard(name="MFX_TIP_module_3")
    MFX_TIP_module_4 = hamilton_mfx_tiprackholder_standard(name="MFX_TIP_module_4")
    MFX_TIP_module_5 = hamilton_mfx_tiprackholder_standard(name="MFX_TIP_module_5")

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
