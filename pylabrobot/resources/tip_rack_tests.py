import os
import tempfile
import unittest

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.lid import Lid
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import NestedTipRack, StandingTipRack, TipRack, TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d


def _make_tip(name: str) -> Tip:
  """Create a vendor-independent tip for resource tests."""
  return Tip(
    name=name,
    has_filter=False,
    maximal_volume=10.0,
    fitting_depth=1.0,
    diameter=1.0,
    size_z=10.0,
  )


class SimpleTipRack(TipRack):
  """Minimal concrete TipRack for testing."""

  def __init__(self, name: str):
    spot = TipSpot(
      name="A1",
      size_x=1.0,
      size_y=1.0,
      make_tip=_make_tip,
    )
    spot.location = Coordinate(0.0, 0.0, 0.0)
    ordered_items = {"A1": spot}
    super().__init__(
      name=name,
      size_x=1.0,
      size_y=1.0,
      size_z=1.0,
      ordered_items=ordered_items,
    )


class TipRackNamingTests(unittest.TestCase):
  """Tests for tip naming behavior in TipSpot/TipRack."""

  def test_get_tip_assigns_unique_names(self):
    rack = SimpleTipRack("my_rack")
    spot = rack.get_item("A1")

    tip1 = spot.make_tip()
    tip2 = spot.make_tip()

    self.assertIsNotNone(tip1.name)
    self.assertIsNotNone(tip2.name)
    self.assertNotEqual(tip1.name, tip2.name)

  def test_set_tip_state_fills_with_named_tips(self):
    rack = SimpleTipRack("my_rack")

    rack.set_tip_state({"A1": True})

    spot = rack.get_item("A1")
    tip = spot.tracker.get_tip()
    self.assertIsNotNone(tip.name)

  def test_deserialize_prototype_with_resource_state(self):
    """A restored spot constructs named tips from a resource prototype."""
    spot = TipSpot("spot", 9, 9, make_tip=_make_tip)
    data = spot.serialize()
    data["prototype_tip"]["rotation"] = {"type": "Rotation", "x": 0, "y": 0, "z": 90}
    data["prototype_tip"]["metadata"] = {"batch": "example"}
    data["prototype_tip"]["location"] = Coordinate(1, 2, 3).serialize()

    restored = TipSpot.deserialize(data)
    first, second = restored.make_tip(), restored.make_tip()
    self.assertIsInstance(first, Tip)
    self.assertNotEqual(first.name, second.name)
    self.assertEqual(first.rotation.z, 90)
    self.assertEqual(first.metadata, {"batch": "example"})
    self.assertIsNone(first.location)


class NestedTipRackTests(unittest.TestCase):
  """A nesting rack keeps its definition through a copy, a round trip and a saved deck."""

  @staticmethod
  def _rack(name: str, with_tips: bool = True) -> StandingTipRack:
    """Create a vendor-independent rack with tips and a known stacking height."""
    return StandingTipRack(
      name=name,
      size_x=30,
      size_y=20,
      size_z=25,
      stacking_z_height=10,
      model="test_tip_rack",
      with_tips=with_tips,
      ordered_items=create_ordered_items_2d(
        TipSpot,
        num_items_x=3,
        num_items_y=2,
        dx=2,
        dy=2,
        dz=25,
        item_dx=8,
        item_dy=8,
        size_x=1,
        size_y=1,
        make_tip=_make_tip,
        name_prefix=name,
      ),
    )

  def test_deserialize_round_trip(self):
    rack = self._rack("rack")

    restored = StandingTipRack.deserialize(rack.serialize())

    self.assertEqual(restored.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(restored.model, rack.model)
    self.assertEqual(restored.num_items, rack.num_items)

  def test_copy_stacks_like_original(self):
    rack = self._rack("rack")
    copied = rack.copy()

    on_rack, on_copy = ResourceStack("on_rack", "z"), ResourceStack("on_copy", "z")
    on_rack.assign_child_resource(rack)
    on_rack.assign_child_resource(self._rack("stacked_on_rack"))
    on_copy.assign_child_resource(copied)
    on_copy.assign_child_resource(self._rack("stacked_on_copy"))

    self.assertEqual(copied.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(
      on_copy.get_resource("stacked_on_copy").location,
      on_rack.get_resource("stacked_on_rack").location,
    )
    stacked = on_rack.get_resource("stacked_on_rack")
    assert stacked.location is not None
    self.assertEqual(stacked.location.z, rack.stacking_z_height)

  def test_save_and_load_deck(self):
    deck = Deck(size_x=1000, size_y=1000, size_z=1000)
    rack = self._rack("rack")
    deck.assign_child_resource(rack, location=Coordinate(100, 100, 0))

    with tempfile.TemporaryDirectory() as tmp_dir:
      fn = os.path.join(tmp_dir, "deck.json")
      deck.save(fn)
      loaded = Deck.load_from_json_file(fn)

    loaded_rack = loaded.get_resource("rack")
    assert isinstance(loaded_rack, StandingTipRack)
    self.assertEqual(loaded_rack.stacking_z_height, rack.stacking_z_height)
    self.assertEqual(loaded_rack.location, rack.location)


class TipRackLidTests(unittest.TestCase):
  def _lid(self, name="lid"):
    return Lid(name, size_x=10, size_y=10, size_z=10, nesting_z_height=2)

  def test_a_lid_seats_on_the_top_face_and_covers_the_rack(self):
    rack = StandingTipRack("rack", size_x=10, size_y=10, size_z=55, ordered_items={})
    self.assertTrue(rack._available_for_tip_handling)
    rack.lid = self._lid()
    self.assertEqual(rack.lid.location, Coordinate(0, 0, 53))
    self.assertFalse(rack._available_for_tip_handling)
    with self.assertRaisesRegex(ValueError, "already has a lid"):
      rack.lid = self._lid("lid_2")

  def test_only_the_top_rack_of_a_stack_is_available(self):
    lower = StandingTipRack("lower", size_x=10, size_y=10, size_z=55, ordered_items={})
    upper = StandingTipRack("upper", size_x=10, size_y=10, size_z=55, ordered_items={})
    stack = ResourceStack("stack", "z")
    stack.assign_child_resource(lower)
    stack.assign_child_resource(upper)
    self.assertFalse(lower._available_for_tip_handling)
    self.assertTrue(upper._available_for_tip_handling)

  def test_a_nested_tip_rack_takes_a_lid(self):
    with self.assertWarns(DeprecationWarning):
      rack = NestedTipRack(
        "rack", size_x=10, size_y=10, size_z=20, stacking_z_height=12, ordered_items={}
      )
    rack.lid = self._lid()
    self.assertEqual(rack.lid.location, Coordinate(0, 0, 18))


class StandingTipRackTests(unittest.TestCase):
  def test_serialize_round_trip(self):
    rack = StandingTipRack(
      "rack",
      size_x=10,
      size_y=10,
      size_z=55,
      ordered_items={},
      stacking_z_height=16,
      frame_height=3,
    )
    restored = Resource.deserialize(rack.serialize())
    assert isinstance(restored, StandingTipRack)
    self.assertEqual(restored.stacking_z_height, 16)
    self.assertEqual(restored.frame_height, 3)
    self.assertEqual(restored, rack)


class TipSpotHoldsItsTip(unittest.TestCase):
  """A tip spot carries its tip as a child."""

  def setUp(self):
    self.rack = NestedTipRackTests._rack("rack")
    self.spot = self.rack.get_item("A1")

  def test_a_racked_tip_is_a_child_of_its_spot(self):
    tip = self.spot.get_tip()
    self.assertIs(tip.parent, self.spot)
    self.assertEqual([child.name for child in self.spot.children], [tip.name])

  def test_a_tip_rests_by_its_collar(self):
    """The tip's top is its collar height above the spot."""
    tip = self.spot.get_tip()
    self.assertEqual(tip.location, Coordinate(0, 0, -tip.get_size_z()))

  def test_taking_the_tip_out_leaves_the_spot_empty(self):
    tip = self.spot.get_tip()
    self.spot.tracker.remove_tip(commit=True)
    self.assertEqual(self.spot.children, [])
    self.assertIsNone(tip.parent)

  def test_a_rolled_back_pickup_puts_the_tip_back(self):
    tip = self.spot.get_tip()
    self.spot.tracker.remove_tip(commit=False)
    self.assertEqual(self.spot.children, [])
    self.spot.tracker.rollback()
    self.assertIs(tip.parent, self.spot)

  def test_a_spot_with_a_tip_is_the_same_spot_without_one(self):
    """What a holder carries is state, so a deck round-trips to an equal deck."""
    empty = NestedTipRackTests._rack("rack", with_tips=False)
    self.assertEqual(self.rack, empty)


class TipSpotHoldsItsTipInTheTree(unittest.TestCase):
  """What `TipSpot.tip` reports is the tree alone: a tip moved by anything is where the tree has it."""

  def setUp(self):
    self.rack = NestedTipRackTests._rack("rack")
    self.spot = self.rack.get_item("A1")

  def test_a_tip_taken_out_of_the_tree_leaves_the_spot_empty(self):
    tip = self.spot.unassign_tip()
    self.assertIsNone(tip.parent)
    self.assertIsNone(self.spot.tip)

  def test_a_tip_put_into_the_tree_is_the_spots_tip(self):
    tip = self.spot.unassign_tip()
    self.spot.assign_tip(tip)
    self.assertIs(self.spot.tip, tip)
    self.assertEqual(tip.location, Coordinate(0, 0, -tip.get_size_z()))

  def test_a_tip_mounted_on_a_shaft_in_the_same_tree_leaves_its_spot(self):
    from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
    from pylabrobot.resources.resource import Resource

    world = Resource("world", size_x=500, size_y=500, size_z=500)
    world.assign_child_resource(self.rack, location=Coordinate.zero())
    shaft = TipMountingShaft("shaft", tip_pickup_mode="core")
    world.assign_child_resource(shaft, location=Coordinate(300, 300, 300))
    tip = self.spot.tip
    assert tip is not None
    shaft.mount_tip(tip)
    self.assertIs(tip.parent, shaft)
    self.assertIsNone(self.spot.tip)

  def test_a_spot_holding_a_tip_is_refused_another(self):
    other = self.rack.get_item("B1").unassign_tip()
    with self.assertRaises(HasTipError):
      self.spot.assign_tip(other)

  def test_a_tip_that_cannot_be_mounted_stays_in_its_spot(self):
    from pylabrobot.resources.n_channel_pipettes import TipMountingShaft

    shaft = TipMountingShaft("shaft", tip_pickup_mode="core")

    def refuse(resource):
      raise RuntimeError("refused")

    shaft.register_will_assign_resource_callback(refuse)
    tip = self.spot.tip
    assert tip is not None
    with self.assertRaises(RuntimeError):
      shaft.mount_tip(tip)
    self.assertIs(tip.parent, self.spot)
    self.assertIs(self.spot.tip, tip)

  def test_a_second_tip_assigned_directly_is_refused(self):
    other = self.rack.get_item("B1").unassign_tip()
    with self.assertRaises(HasTipError):
      self.spot.assign_child_resource(other, location=Coordinate.zero())
