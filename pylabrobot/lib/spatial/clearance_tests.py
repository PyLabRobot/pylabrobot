import unittest

from pylabrobot.lib.spatial.clearance import get_longest_tip_overhang
from pylabrobot.resources.hamilton import (
  TIP_CAR_480_A00,
  STARDeck,
  hamilton_96_tiprack_1000uL,
  hamilton_tip_300uL,
)
from pylabrobot.resources.hamilton.core_gripper_tools import hamilton_core_gripper_tool
from pylabrobot.resources.resource import Resource


class GetLongestTipOverhangTests(unittest.TestCase):
  def test_nothing_that_is_a_tip_is_none(self):
    holder = Resource(name="holder", size_x=10, size_y=10, size_z=10)
    holder.assign_child_resource(hamilton_core_gripper_tool("tool"), location=None)
    self.assertIsNone(get_longest_tip_overhang(holder))

  def test_a_mounted_tip_counts_as_itself(self):
    holder = Resource(name="holder", size_x=10, size_y=10, size_z=10)
    tip = hamilton_tip_300uL(name="tip")
    holder.assign_child_resource(tip, location=None)
    overhang = get_longest_tip_overhang(holder)
    assert overhang is not None
    self.assertAlmostEqual(overhang, tip.get_size_z() - tip.fitting_depth)

  def test_the_longest_rack_on_the_deck_decides(self):
    deck = STARDeck()
    # The teaching needle stands on every deck, as long as a 300 uL tip.
    needle = get_longest_tip_overhang(deck)
    assert needle is not None
    self.assertAlmostEqual(needle, 51.9)
    carrier = TIP_CAR_480_A00(name="tip_carrier")
    carrier[0] = hamilton_96_tiprack_1000uL(name="rack")
    deck.assign_child_resource(carrier, track=20)
    longest = get_longest_tip_overhang(deck)
    assert longest is not None
    self.assertAlmostEqual(longest, 87.1)
