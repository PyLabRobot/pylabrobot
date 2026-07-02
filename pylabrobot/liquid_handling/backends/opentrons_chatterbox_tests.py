"""Tests for OpentronsOT2ChatterboxBackend.

Deliberately does NOT importorskip("ot_api"): running the real backend logic with
no hardware and no ot_api library is the whole point of the chatterbox.
"""

import unittest
import warnings

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import OpentronsOT2ChatterboxBackend
from pylabrobot.liquid_handling.errors import NoChannelError
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import CellTreat_96_wellplate_350ul_Fb
from pylabrobot.resources.opentrons import (
  OTDeck,
  opentrons_96_filtertiprack_20ul,
  opentrons_96_tiprack_300ul,
)


def _names(backend: OpentronsOT2ChatterboxBackend):
  return [call[0] for call in backend.commands]


class OpentronsChatterboxTests(unittest.IsolatedAsyncioTestCase):
  """Direct tests: the chatterbox runs the real backend with no ot_api."""

  async def asyncSetUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_single_gen2",
      right_pipette_name="p20_single_gen2",
      verbose=False,
    )
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()
    self.tips = opentrons_96_filtertiprack_20ul(name="tips")
    self.deck.assign_child_at_slot(self.tips, slot=1)
    self.plate = CellTreat_96_wellplate_350ul_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=2)

  async def asyncTearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_setup_resolves_two_channels_without_ot_api(self):
    """setup() runs through the recorder and resolves both mounted pipettes."""
    self.assertEqual(self.backend.num_channels, 2)
    assert self.backend.left_pipette is not None and self.backend.right_pipette is not None
    self.assertEqual(self.backend.left_pipette["name"], "p20_single_gen2")

  async def test_full_protocol_records_one_wire_call_per_operation(self):
    """A pickup -> aspirate -> dispense -> trash-discard records exactly one
    wire call each, via the real backend logic."""
    self.plate.get_well("A1").tracker.set_volume(15)
    await self.lh.pick_up_tips(self.tips["A1"])
    await self.lh.aspirate(self.plate["A1"], vols=[10])
    await self.lh.dispense(self.plate["B1"], vols=[10])
    await self.lh.discard_tips()

    names = _names(self.backend)
    self.assertEqual(names.count("lh.pick_up_tip"), 1)
    self.assertEqual(names.count("lh.aspirate_in_place"), 1)
    self.assertEqual(names.count("lh.dispense_in_place"), 1)
    # api_version defaults to 7.1.0, so the discard routes through the trash addressable area
    self.assertEqual(names.count("lh.move_to_addressable_area_for_drop_tip"), 1)
    self.assertEqual(names.count("lh.drop_tip_in_place"), 1)

  def test_unknown_pipette_name_raises(self):
    """An unrecognised pipette name is rejected at construction."""
    with self.assertRaises(ValueError):
      OpentronsOT2ChatterboxBackend(left_pipette_name="not_a_pipette")

  def test_serialize_includes_pipettes(self):
    """serialize() captures the mounted-pipette names (None for an empty mount)."""
    backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_single_gen2", right_pipette_name=None, verbose=False
    )
    serialized = backend.serialize()
    self.assertEqual(serialized["left_pipette_name"], "p20_single_gen2")
    self.assertIsNone(serialized["right_pipette_name"])


class OpentronsChatterboxHead8Tests(unittest.IsolatedAsyncioTestCase):
  """Multi-channel (head8) column operations, dry-run through the chatterbox."""

  async def asyncSetUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_multi_gen2",
      right_pipette_name="p300_single_gen2",
      verbose=False,
    )
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()
    self.tips = opentrons_96_filtertiprack_20ul(name="tips")
    self.deck.assign_child_at_slot(self.tips, slot=1)
    self.plate = CellTreat_96_wellplate_350ul_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=2)

  async def asyncTearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_eight_channel_column_issues_one_command_each_and_tracks_all(self):
    """A full-column pickup -> aspirate -> dispense on the 8 multi channels issues
    exactly one ot_api command each, tracks all 8 channels, and fills the column."""
    rows = "ABCDEFGH"
    channels = list(range(8))
    for r in rows:
      self.plate.get_well(f"{r}1").tracker.set_volume(20)

    await self.lh.pick_up_tips([self.tips.get_item(f"{r}1") for r in rows], use_channels=channels)
    await self.lh.aspirate(
      [self.plate.get_item(f"{r}1") for r in rows], vols=[5.0] * 8, use_channels=channels
    )
    await self.lh.dispense(
      [self.plate.get_item(f"{r}2") for r in rows], vols=[5.0] * 8, use_channels=channels
    )

    names = [call[0] for call in self.backend.commands]
    self.assertEqual(names.count("lh.pick_up_tip"), 1)
    self.assertEqual(names.count("lh.aspirate_in_place"), 1)
    self.assertEqual(names.count("lh.dispense_in_place"), 1)
    self.assertTrue(all(self.lh.head[c].has_tip for c in channels))
    for r in rows:
      self.assertEqual(self.plate.get_item(f"{r}2").tracker.get_used_volume(), 5.0)

  async def test_channels_spanning_two_mounts_is_rejected(self):
    """Channels addressing both the multi (0) and the single (8) cannot be one
    command - the OT-2 drives a single mount per call. The resolver only pairs ops
    with channels, so plain sentinels stand in for the ops here."""
    ops = [object(), object()]
    with self.assertRaises(NoChannelError):
      self.backend._resolve_pipette_and_primary(ops, use_channels=[0, 8])  # type: ignore[arg-type]

  async def test_back_anchored_pickup_is_rejected(self):
    """use_channels must be a channel-0-anchored block; [1..7] (leaving row A) is rejected."""
    with self.assertRaises(ValueError):
      await self.lh.pick_up_tips(
        [self.tips.get_item(f"{r}1") for r in "BCDEFGH"], use_channels=list(range(1, 8))
      )

  async def test_pickup_grabbing_undeclared_tips_below_is_rejected(self):
    """Picking A1:F1 from a full column would also grab the occupied G1/H1 below it within
    the head's 8-nozzle reach; rejected by default."""
    with self.assertRaises(ValueError):
      await self.lh.pick_up_tips(
        [self.tips.get_item(f"{r}1") for r in "ABCDEF"], use_channels=list(range(6))
      )

  async def test_allow_undeclared_absorbs_grabbed_tips(self):
    """allow_undeclared_tip_pickup=True absorbs the extra grabbed tips into tracking (with a
    warning) so all eight nozzles are accounted for."""
    with warnings.catch_warnings(record=True) as caught:
      warnings.simplefilter("always")
      await self.lh.pick_up_tips(
        [self.tips.get_item(f"{r}1") for r in "ABCDEF"],
        use_channels=list(range(6)),
        allow_undeclared_tip_pickup=True,
      )
    self.assertTrue(all(self.lh.head[c].has_tip for c in range(8)))
    self.assertTrue(any("undeclared tips" in str(w.message) for w in caught))


class _FarTarget:
  """Stub resource that always reports a location far beyond the gantry envelope."""

  def get_location_wrt(self, *args, **kwargs):
    return Coordinate(150, 800, 5)


class _FarOp:
  def __init__(self):
    self.resource = _FarTarget()
    self.offset = Coordinate(0, 0, 0)


class OpentronsChatterboxReachTests(unittest.IsolatedAsyncioTestCase):
  """Center-based reachability check (OT2RobotGeometry wired into the operations)."""

  async def asyncSetUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_multi_gen2", right_pipette_name="p300_single_gen2", verbose=False
    )
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()
    self.tips = opentrons_96_filtertiprack_20ul(name="tips")
    self.deck.assign_child_at_slot(self.tips, slot=5)

  async def asyncTearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_can_reach_position_central_yes_far_no(self):
    """A central deck coordinate is reachable; one far past the back envelope is not -
    for both a multi nozzle (channel 0) and the single (channel 8)."""
    central, far = Coordinate(150, 150, 5), Coordinate(150, 800, 5)
    self.assertTrue(self.backend.can_reach_position(0, central))
    self.assertTrue(self.backend.can_reach_position(8, central))
    self.assertFalse(self.backend.can_reach_position(0, far))
    self.assertFalse(self.backend.can_reach_position(8, far))

  async def test_out_of_envelope_target_raises(self):
    """ensure_can_reach_position rejects a target outside the gantry envelope."""
    with self.assertRaises(ValueError):
      self.backend.ensure_can_reach_position([0], [_FarOp()], "pick_up_tips")  # type: ignore[list-item]

  async def test_reachable_head8_column_pickup_passes(self):
    """A normal on-deck head8 column pickup passes the reach guard and runs."""
    await self.lh.pick_up_tips(
      [self.tips.get_item(f"{r}1") for r in "ABCDEFGH"], use_channels=list(range(8))
    )
    self.assertTrue(all(self.lh.head[c].has_tip for c in range(8)))

  async def test_trash_discard_skips_reach_check(self):
    """Discarding to the fixed trash is exempt from the reach check (it routes through the
    addressable area, not a reach-bounded move), so it is not falsely rejected."""
    await self.lh.pick_up_tips(
      [self.tips.get_item(f"{r}1") for r in "ABCDEFGH"], use_channels=list(range(8))
    )
    await self.lh.discard_tips()
    self.assertFalse(any(self.lh.head[c].has_tip for c in range(8)))


class OpentronsChatterboxPartialPickupTests(unittest.IsolatedAsyncioTestCase):
  """Careful coverage of partial head8 pickup: a channel-0-anchored block of k < 8 channels.

  Allowed only when the tipspots the unused nozzles cover are empty; the unused nozzles may
  overhang past the last row into empty space. Exercises the deck-accessibility guard at both
  the front (slot 1) and back (slot 10) edges. (Surrounding-resource collision - e.g. a tall
  resource in an adjacent slot the head overhangs into - is NOT modelled and not tested here.)
  """

  async def asyncSetUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)
    self.backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_multi_gen2", right_pipette_name="p300_single_gen2", verbose=False
    )
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

  async def asyncTearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def _rack(self, slot, present_rows):
    """A 20 uL tiprack on ``slot`` whose column-1 tips exist only on ``present_rows``."""
    rack = opentrons_96_filtertiprack_20ul(name=f"tips_{slot}")
    self.deck.assign_child_at_slot(rack, slot=slot)
    for r in "ABCDEFGH":
      if r not in present_rows:
        rack.get_item(f"{r}1").tracker.remove_tip(commit=True)
    return rack

  async def test_partial_pickup_picks_exactly_k(self):
    """For k = 1..7 a channel-0-anchored block picks exactly k tips (channels 0..k-1) in one
    command, leaving the unused channels empty - when the rows below the selection are empty."""
    rows = "ABCDEFGH"
    for k in range(1, 8):
      with self.subTest(k=k):
        rack = self._rack(slot=k, present_rows=rows[:k])
        mark = len(self.backend.commands)
        await self.lh.pick_up_tips(
          [rack.get_item(f"{r}1") for r in rows[:k]], use_channels=list(range(k))
        )
        names = [n for n, _, _ in self.backend.commands[mark:]]
        self.assertEqual(names.count("lh.pick_up_tip"), 1)
        self.assertTrue(all(self.lh.head[c].has_tip for c in range(k)))
        self.assertFalse(any(self.lh.head[c].has_tip for c in range(k, 8)))
        await self.lh.return_tips()

  async def test_partial_pickup_of_bottom_rows_overhangs_into_empty_space(self):
    """Picking the bottom rows E-H with channels 0-3 (anchored at E1) is allowed at both the
    front (slot 1) and back (slot 10) edge - the unused nozzles overhang past row H, over no tips."""
    for slot in (1, 10):
      with self.subTest(slot=slot):
        rack = self._rack(slot=slot, present_rows="EFGH")
        await self.lh.pick_up_tips(
          [rack.get_item(f"{r}1") for r in "EFGH"], use_channels=[0, 1, 2, 3]
        )
        self.assertTrue(all(self.lh.head[c].has_tip for c in range(4)))
        self.assertFalse(any(self.lh.head[c].has_tip for c in range(4, 8)))
        await self.lh.return_tips()

  async def test_partial_pickup_rejected_when_a_covered_tipspot_is_occupied(self):
    """Picking A-C (channels 0-2) is rejected when row D - covered by unused nozzle 3 - still
    holds a tip: the head would grab it too."""
    rack = self._rack(slot=2, present_rows="ABCD")
    with self.assertRaises(ValueError):
      await self.lh.pick_up_tips([rack.get_item(f"{r}1") for r in "ABC"], use_channels=[0, 1, 2])

  async def test_overhang_into_tall_adjacent_resource_is_rejected(self):
    """Guard 2 (surrounding-resource): a partial pickup at slot 10 overhangs into slot 7. Allowed
    when slot 7 is empty; rejected once slot 7 holds a tall tiprack the head footprint would hit."""
    rack = self._rack(slot=10, present_rows="EFGH")
    await self.lh.pick_up_tips(
      [rack.get_item(f"{r}1") for r in "EFGH"], use_channels=[0, 1, 2, 3]
    )  # slot 7 empty -> allowed
    await self.lh.return_tips()

    self.deck.assign_child_at_slot(opentrons_96_filtertiprack_20ul(name="tall_neighbour"), slot=7)
    with self.assertRaises(ValueError):
      await self.lh.pick_up_tips(
        [rack.get_item(f"{r}1") for r in "EFGH"], use_channels=[0, 1, 2, 3]
      )

  async def test_overhang_clears_short_adjacent_resource(self):
    """The collision check is z-aware: the same slot-10 overhang is allowed when slot 7 holds only
    a short resource (a 96-well plate) whose top sits below the head's path."""
    rack = self._rack(slot=10, present_rows="EFGH")
    self.deck.assign_child_at_slot(CellTreat_96_wellplate_350ul_Fb(name="short_neighbour"), slot=7)
    await self.lh.pick_up_tips([rack.get_item(f"{r}1") for r in "EFGH"], use_channels=[0, 1, 2, 3])
    self.assertTrue(all(self.lh.head[c].has_tip for c in range(4)))

  async def test_collision_z_reference_is_the_nozzle_engagement_height(self):
    """Pins the collision z-reference to where the nozzles engage (tip top - fitting depth), not
    the tipspot anchor (the tip bottom). For a 300 uL tip the tip bottom sits near the deck, so the
    buggy reference would drive the z threshold negative and wrongly reject even a short neighbour.
    So a 300 uL partial pickup over a short 96-well plate must be ALLOWED - cleared only with the
    correct reference. (This is the test that would fail if the z-fix were reverted.)"""
    backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p300_multi_gen2", right_pipette_name="p20_single_gen2", verbose=False
    )
    deck = OTDeck()
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
    rack = opentrons_96_tiprack_300ul(name="t300")
    deck.assign_child_at_slot(rack, slot=10)
    for r in "ABCD":
      rack.get_item(f"{r}1").tracker.remove_tip(commit=True)
    deck.assign_child_at_slot(CellTreat_96_wellplate_350ul_Fb(name="short"), slot=7)
    await lh.pick_up_tips([rack.get_item(f"{r}1") for r in "EFGH"], use_channels=[0, 1, 2, 3])
    self.assertTrue(all(lh.head[c].has_tip for c in range(4)))


class _StubResource:
  """A deck resource with a fixed footprint and height, for the collision-math unit test."""

  children: list = []

  def __init__(self, name, x, y, z, sx, sy, sz, parent=None):
    self.name = name
    self.parent = parent
    self._loc = Coordinate(x, y, z)
    self._sx, self._sy, self._sz = sx, sy, sz

  def get_absolute_location(self, *args, **kwargs):
    return self._loc

  def get_absolute_size_x(self):
    return self._sx

  def get_absolute_size_y(self):
    return self._sy

  def get_absolute_size_z(self):
    return self._sz


class _StubTip:
  total_tip_length = 39.2
  fitting_depth = 8.25


class _StubPickup:
  def __init__(self, x, y, z, parent):
    self.resource = _StubResource("primary_spot", x, y, z, 0, 0, 0, parent=parent)
    self.tip = _StubTip()


class OpentronsCollisionMathTests(unittest.TestCase):
  """Unit test of _check_head8_surrounding_resources with synthetic coordinates, decoupled from the
  real deck geometry: verifies the x-y bounding-box overlap and the z-height threshold directly."""

  def test_box_overlap_and_z_threshold(self):
    backend = OpentronsOT2ChatterboxBackend(
      left_pipette_name="p20_multi_gen2", right_pipette_name="p20_single_gen2", verbose=False
    )
    target_rack = object()
    # primary tip at (130, 380, 25.5): col_x=130, pickup_z = 25.5 + 39.2 - 8.25 = 56.45;
    # head box x [125, 135], y [380 - 7*9 - 5, 385] = [312, 385]; collide iff top z >= 46.45.
    primary = _StubPickup(130, 380, 25.5, parent=target_rack)

    tall_overlap = _StubResource("tall_overlap", 120, 340, 0, 20, 20, 64.7)  # overlaps + tall
    short_overlap = _StubResource("short_overlap", 120, 340, 0, 20, 20, 14.3)  # overlaps but short
    tall_far = _StubResource("tall_far", 200, 340, 0, 20, 20, 64.7)  # tall but no x overlap

    class _Holder:
      def __init__(self, res):
        self.children = [res]

    class _Deck:
      children = [_Holder(tall_overlap), _Holder(short_overlap), _Holder(tall_far)]

    backend.set_deck(_Deck())  # type: ignore[arg-type]
    names = [r.name for r in backend._check_head8_surrounding_resources([primary], [0])]  # type: ignore[list-item]
    self.assertIn("tall_overlap", names)  # overlap + tall enough -> collision
    self.assertNotIn("short_overlap", names)  # overlap but below the head's path -> cleared
    self.assertNotIn("tall_far", names)  # tall but outside the x band -> no collision


if __name__ == "__main__":
  unittest.main()
