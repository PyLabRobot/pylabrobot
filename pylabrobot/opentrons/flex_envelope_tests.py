"""Tests for the native Flex operating envelope + its wiring into the heads.

``envelope.py`` derives every reach cap from grounded OT-3 primitives; ``checks.py``
is the pre-dispatch verification surface. These tests pin the derived caps to the
opentrons **8.8.1** shared-data the robot actually runs, and assert the computed
traversal plane replaces the hardcoded magic number.
"""

import asyncio
import unittest
from typing import List, Tuple

from pylabrobot.opentrons.checks import traversal_z
from pylabrobot.opentrons.envelope import FLEX_ENVELOPE
from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead8
from pylabrobot.opentrons.transport import ChatterboxTransport
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.resources.opentrons.flex_deck import FlexDeck


def _flex_head8() -> Tuple[OpentronsFlex, ChatterboxTransport, FlexHead8]:
  transport = ChatterboxTransport(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
  flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
  asyncio.run(flex.setup())
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, transport, head


def _commands_of(transport: ChatterboxTransport, command_type: str) -> List[dict]:
  return [c for c in transport.commands if c["commandType"] == command_type]


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
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    deck.assign_child_at_slot(plate, "C2")
    self.assertAlmostEqual(traversal_z(deck), 109.0)


class TestComputedTraversalPlane(unittest.TestCase):
  """A lateral jog defaults its ``minimumZHeight`` to the COMPUTED tip-safe plane
  (tallest labware top + arc margin), not a hardcoded 120.0 magic number."""

  def test_move_to_uses_computed_traversal_not_120(self):
    flex, transport, head = _flex_head8()
    try:
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(plate, "C2")

      expected = traversal_z(flex.deck)
      self.assertNotAlmostEqual(expected, 120.0, msg="test needs labware whose plane != 120")

      asyncio.run(head.move_to(x=100.0, y=100.0, z=50.0))

      move_cmds = _commands_of(transport, "moveToCoordinates")
      self.assertEqual(len(move_cmds), 1)
      self.assertAlmostEqual(move_cmds[0]["params"]["minimumZHeight"], expected)
    finally:
      asyncio.run(flex.stop())


class TestTrashDropArcsHighEnough(unittest.TestCase):
  """The move to the trash after a dispense must arc at the computed traversal
  plane, not the engine's default -- otherwise it can travel too low and clip
  labware the robot was never told about."""

  def test_discard_tips_move_carries_computed_minimum_z_height(self):
    from pylabrobot.resources import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul

    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      trash = flex.deck.get_trash_area()

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.discard_tips(trash))

      move = next(
        c for c in transport.commands if c["commandType"] == "moveToAddressableAreaForDropTip"
      )
      self.assertAlmostEqual(move["params"]["minimumZHeight"], traversal_z(flex.deck))
    finally:
      asyncio.run(flex.stop())


class TestBetweenSlotArcGuard(unittest.TestCase):
  """A pipetting move that crosses to a different slot is prefixed with a safe
  high moveToWell (>= the tip-rack floor); a move within the same labware is not."""

  def setUp(self):
    from pylabrobot.resources import set_tip_tracking, set_volume_tracking

    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    from pylabrobot.resources import set_tip_tracking, set_volume_tracking

    set_tip_tracking(False)
    set_volume_tracking(False)

  def _setup(self):
    from pylabrobot.resources import cor_96_wellplate_360uL_Fb
    from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul

    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    for w in plate.get_all_items():
      w.tracker.set_volume(100.0)
    return flex, transport, head, rack, plate

  def _move_to_wells(self, transport):
    return [c for c in transport.commands if c["commandType"] == "moveToWell"]

  def test_crossing_to_a_new_slot_arcs_high_first(self):
    flex, transport, head, rack, plate = self._setup()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))  # over the rack (C1)
      before = len(self._move_to_wells(transport))
      asyncio.run(head.aspirate(plate.column(0), volume=50))  # -> plate (C2), a new slot

      moves = self._move_to_wells(transport)
      self.assertEqual(
        len(moves), before + 1, "one safe move should precede the cross-slot aspirate"
      )
      self.assertAlmostEqual(moves[-1]["params"]["minimumZHeight"], traversal_z(flex.deck))
      # the safe move comes immediately before the aspirate
      types = [c["commandType"] for c in transport.commands]
      self.assertEqual(types[types.index("aspirate") - 1], "moveToWell")
    finally:
      asyncio.run(flex.stop())

  def test_moving_within_the_same_labware_does_not_arc_high(self):
    flex, transport, head, rack, plate = self._setup()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate.column(0), volume=50))  # cross-slot -> one safe move
      n = len(self._move_to_wells(transport))
      asyncio.run(head.dispense(plate.column(1), volume=50))  # same plate -> no new safe move
      self.assertEqual(len(self._move_to_wells(transport)), n, "within-slot move must not arc high")
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()
