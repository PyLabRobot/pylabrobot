import unittest
from typing import cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import hamilton_96_tiprack_300uL, hamilton_tip_300uL
from pylabrobot.resources.n_channel_pipettes import TipMountingShaft
from pylabrobot.resources.tip_rack import TipRack


class TipSpotHoldsItsTip(unittest.TestCase):
  """A tip spot carries its tip, and says where it sits."""

  def setUp(self):
    self.rack = cast(TipRack, hamilton_96_tiprack_300uL("rack"))
    self.spot = self.rack.get_item("A1")

  def test_a_racked_tip_is_a_child_of_its_spot(self):
    tip = self.spot.get_tip()
    self.assertIs(tip.parent, self.spot)
    self.assertEqual([child.name for child in self.spot.children], [tip.name])

  def test_a_tip_hangs_by_its_collar(self):
    """The collar's underside rests on the spot, so the rest of the tip hangs below it."""
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


class ShaftHoldsItsTip(unittest.TestCase):
  """A mounting shaft holds a tool the same way, gripping it by the fitting depth."""

  def setUp(self):
    self.shaft = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.tip = hamilton_tip_300uL(name="tip")

  def test_a_mounted_tip_sits_by_its_fitting_depth(self):
    self.shaft.mount_tip(self.tip)
    self.assertTrue(self.shaft.has_tip())
    self.assertEqual(
      self.tip.location,
      Coordinate(
        (self.shaft.get_size_x() - self.tip.get_size_x()) / 2,
        (self.shaft.get_size_y() - self.tip.get_size_y()) / 2,
        self.tip.fitting_depth - self.tip.total_tip_length,
      ),
    )

  def test_the_tip_bottom_is_what_reaches_below_the_channel(self):
    """Part of the tip is up inside the channel, so it does not reach its whole length down."""
    self.shaft.mount_tip(self.tip)
    self.assertEqual(self.shaft.tip_bottom().z, self.tip.fitting_depth - self.tip.total_tip_length)
    self.shaft.release_tip()
    self.assertEqual(self.shaft.tip_bottom(), Coordinate.zero())

  def test_an_empty_shaft_equals_one_carrying_a_tip(self):
    other = TipMountingShaft(name="shaft", tip_pickup_mode="core")
    self.shaft.mount_tip(self.tip)
    self.assertEqual(self.shaft, other)
