"""Tests for the native Flex operating envelope + its wiring into the heads.

``envelope.py`` derives every reach cap from grounded OT-3 primitives; ``checks.py``
is the pre-dispatch verification surface. These tests pin the derived caps to the
opentrons **8.8.1** shared-data the robot actually runs, and assert the computed
traversal plane replaces the hardcoded magic number.
"""

import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, _Call

from pylabrobot.opentrons.flex.checks import traversal_z
from pylabrobot.opentrons.flex.envelope import FLEX_ENVELOPE
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_head import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.opentrons.flex_deck import FlexDeck


async def _flex_head8(
  test_case: unittest.IsolatedAsyncioTestCase,
) -> Tuple[Flex, AsyncMock, FlexHead8]:
  api = make_api(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, api, head


def _commands_of(api: AsyncMock, command_type: str) -> List[_Call]:
  return [c for c in api.submit_command.await_args_list if c.args[1] == command_type]


class TestRearCapGroundedTo881(unittest.TestCase):
  """The 8-channel rear reach cap is ``deck_extent_y + paddingOffsets.rear``.

  The rear padding is version-specific: opentrons 8.3.0 = -177.42, but the robot
  runs **8.8.1** where it is -169.42, giving a rear cap of 324.38. The stale
  8.3.0 value (316.38) must not be what the envelope carries.
  """

  def test_rear_cap_matches_881_shared_data(self):
    self.assertAlmostEqual(FLEX_ENVELOPE.padding_rear, -169.42)
    self.assertAlmostEqual(FLEX_ENVELOPE.rear_cap_y, 324.38)


class TestUnconditionalTiprackFloor(unittest.TestCase):
  """The travel plane never drops below a tip rack (99 + 10 margin = 109), even on
  a deck the model shows as empty or holding only short labware -- a rack that is
  present but unmodeled must still be cleared."""

  def test_empty_deck_still_clears_a_tiprack(self):
    self.assertAlmostEqual(traversal_z(FlexDeck()), 109.0)

  def test_short_labware_does_not_lower_the_floor(self):
    deck = FlexDeck()
    plate = cor_96_wellplate_360uL_Fb(name="plate")  # ~14 mm tall, well below a rack
    deck.assign_child_at_slot(plate, "C2")
    self.assertAlmostEqual(traversal_z(deck), 109.0)


class TestComputedTraversalPlane(unittest.IsolatedAsyncioTestCase):
  """A lateral jog defaults its ``minimumZHeight`` to the COMPUTED tip-safe plane
  (tallest labware top + arc margin), not a hardcoded 120.0 magic number."""

  async def test_move_to_uses_computed_traversal_not_120(self):
    flex, api, head = await _flex_head8(self)
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(plate, "C2")

    expected = traversal_z(flex.deck)
    self.assertNotAlmostEqual(expected, 120.0, msg="test needs labware whose plane != 120")

    await head.move_to(x=100.0, y=100.0, z=50.0)

    move_cmds = _commands_of(api, "moveToCoordinates")
    self.assertEqual(len(move_cmds), 1)
    self.assertAlmostEqual(move_cmds[0].args[2]["minimumZHeight"], expected)


class TestTrashDropArcsHighEnough(unittest.IsolatedAsyncioTestCase):
  """The move to the trash after a dispense must arc at the computed traversal
  plane, not the engine's default -- otherwise it can travel too low and clip
  labware the robot was never told about."""

  async def test_discard_tips_move_carries_computed_minimum_z_height(self):
    from pylabrobot.resources import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul

    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    trash = flex.deck.get_trash_area()

    await head.pick_up_tips(rack, column=0)
    await head.discard_tips(trash)

    move = next(
      c
      for c in api.submit_command.await_args_list
      if c.args[1] == "moveToAddressableAreaForDropTip"
    )
    self.assertAlmostEqual(move.args[2]["minimumZHeight"], traversal_z(flex.deck))


class TestBetweenSlotArcGuard(unittest.IsolatedAsyncioTestCase):
  """A pipetting move that crosses to a different slot is prefixed with a safe
  coordinate move (>= the tip-rack floor), including moves within a plate."""

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking, set_volume_tracking

    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    from pylabrobot.resources import set_tip_tracking, set_volume_tracking

    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    from pylabrobot.resources import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul

    self.flex, self.api, self.head = await _flex_head8(self)
    self.rack = flex_96_tiprack_50ul(name="rack")
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.flex.deck.assign_child_at_slot(self.rack, "C1")
    self.flex.deck.assign_child_at_slot(self.plate, "C2")
    for w in self.plate.get_all_items():
      w.tracker.set_volume(100.0)

  def _coordinate_moves(self, api):
    return [c for c in api.submit_command.await_args_list if c.args[1] == "moveToCoordinates"]

  async def test_crossing_to_a_new_slot_arcs_high_first(self):
    await self.head.pick_up_tips(self.rack, column=0)  # over the rack (C1)
    before = len(self._coordinate_moves(self.api))
    await self.head.aspirate(self.plate.column(0), volume=50)  # -> plate (C2), a new slot

    moves = self._coordinate_moves(self.api)
    self.assertEqual(len(moves), before + 1, "one safe move should precede the cross-slot aspirate")
    self.assertAlmostEqual(moves[-1].args[2]["minimumZHeight"], traversal_z(self.flex.deck))
    # Vertical descent immediately precedes aspiration.
    types = [c.args[1] for c in self.api.submit_command.await_args_list]
    self.assertEqual(types[types.index("aspirateInPlace") - 1], "moveRelative")

  async def test_moving_within_the_same_labware_also_arcs_high(self):
    await self.head.pick_up_tips(self.rack, column=0)
    await self.head.aspirate(self.plate.column(0), volume=50)  # cross-slot -> one safe move
    n = len(self._coordinate_moves(self.api))
    await self.head.dispense(self.plate.column(1), volume=50)  # same plate -> safe coordinate move
    self.assertEqual(
      len(self._coordinate_moves(self.api)), n + 1, "within-slot move must also arc high"
    )


if __name__ == "__main__":
  unittest.main()
