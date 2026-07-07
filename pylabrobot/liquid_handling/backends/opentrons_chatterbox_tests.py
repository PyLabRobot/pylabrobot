"""Tests for OpentronsOT2ChatterboxBackend.

Deliberately does NOT importorskip("ot_api"): running the real backend logic with
no hardware and no ot_api library is the whole point of the chatterbox.
"""

import unittest
import warnings

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import OpentronsOT2ChatterboxBackend
from pylabrobot.liquid_handling.errors import NoChannelError
from pylabrobot.resources import set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import CellTreat_96_wellplate_350ul_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


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


if __name__ == "__main__":
  unittest.main()
