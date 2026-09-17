import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot


class SimpleTipRack(TipRack):
  """Minimal concrete TipRack for testing."""

  def __init__(self, name: str):
    spot = TipSpot(
      name="A1",
      size_x=1.0,
      size_y=1.0,
      make_tip=lambda name: Tip(False, 10.0, 10.0, 1.0, name=name),
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


class TipSpotHoldsItsTip(unittest.TestCase):
  """A tip spot carries its tip as a child."""

  def setUp(self):
    self.rack = cast(TipRack, hamilton_96_tiprack_300uL("rack"))
    self.spot = self.rack.get_item("A1")

  def test_a_racked_tip_is_a_child_of_its_spot(self):
    tip = self.spot.get_tip()
    self.assertIs(tip.parent, self.spot)
    self.assertEqual([child.name for child in self.spot.children], [tip.name])

  def test_a_tip_rests_by_its_collar(self):
    """The tip's top is its collar height above the spot."""
    tip = self.spot.get_tip()
    self.assertEqual(tip.location, Coordinate(-0.5, -0.5, tip.collar_height - tip.total_tip_length))

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
    empty = cast(TipRack, hamilton_96_tiprack_300uL("rack", with_tips=False))
    self.assertEqual(self.rack, empty)


class TipSpotHoldsItsTipInTheTree(unittest.TestCase):
  """What `TipSpot.tip` reports is the tree alone: a tip moved by anything is where the tree has it."""

  def setUp(self):
    self.rack = cast(TipRack, hamilton_96_tiprack_300uL("rack"))
    self.spot = self.rack.get_item("A1")

  def test_a_tip_taken_out_of_the_tree_leaves_the_spot_empty(self):
    tip = self.spot.unassign_tip()
    self.assertIsNone(tip.parent)
    self.assertIsNone(self.spot.tip)

  def test_a_tip_put_into_the_tree_is_the_spots_tip(self):
    tip = self.spot.unassign_tip()
    self.spot.assign_tip(tip)
    self.assertIs(self.spot.tip, tip)
    self.assertEqual(tip.location, Coordinate(-0.5, -0.5, tip.collar_height - tip.total_tip_length))

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
