"""Tests for OpentronsOT2ChatterboxBackend.

Deliberately does NOT importorskip("ot_api"): running the real backend logic with
no hardware and no ot_api library is the whole point of the chatterbox.
"""

import unittest

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends import (
  OpentronsOT2ChatterboxBackend,
  OpentronsOT2Simulator,
)
from pylabrobot.legacy.liquid_handling.standard import Mix
from pylabrobot.resources import (
  does_tip_tracking,
  does_volume_tracking,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.celltreat import (
  CellTreat_96_wellplate_350ul_Fb,
  celltreat_96_wellplate_350uL_Fb,
)
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


class OpentronsChatterboxVsSimulatorTests(unittest.IsolatedAsyncioTestCase):
  """Differential audit: the chatterbox must produce the same tracked outcome as
  the reference OpentronsOT2Simulator on the single-channel overlap. (The Simulator
  is single-channel by construction, so the multi-channel head is out of scope here.)"""

  async def asyncSetUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  async def asyncTearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def _run_single_channel_protocol(self, backend):
    deck = OTDeck()
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
    tips = opentrons_96_filtertiprack_20ul(name="tips")
    deck.assign_child_at_slot(tips, slot=1)
    plate = CellTreat_96_wellplate_350ul_Fb(name="plate")
    deck.assign_child_at_slot(plate, slot=2)
    plate.get_well("A1").tracker.set_volume(15)

    await lh.pick_up_tips(tips["A1"])
    await lh.aspirate(plate["A1"], vols=[10])
    await lh.dispense(plate["B1"], vols=[10])

    outcome = (
      lh.head[0].has_tip,
      round(plate.get_well("A1").tracker.get_used_volume(), 3),
      round(plate.get_well("B1").tracker.get_used_volume(), 3),
    )
    await lh.stop()
    return outcome

  async def test_chatterbox_matches_simulator_single_channel(self):
    simulator_outcome = await self._run_single_channel_protocol(
      OpentronsOT2Simulator(
        left_pipette_name="p20_single_gen2", right_pipette_name="p20_single_gen2"
      )
    )
    chatterbox_outcome = await self._run_single_channel_protocol(
      OpentronsOT2ChatterboxBackend(
        left_pipette_name="p20_single_gen2", right_pipette_name="p20_single_gen2", verbose=False
      )
    )
    self.assertEqual(simulator_outcome, (True, 5.0, 10.0))
    self.assertEqual(chatterbox_outcome, simulator_outcome)


class OpentronsFixedHeadChatterboxTests(unittest.IsolatedAsyncioTestCase):
  """Exercise full-head operation through the normal frontend and recorded transport."""

  async def test_both_models_and_mounts_preserve_eight_well_state_and_command_sequence(self):
    """Pickup, mixing, transfer, and discard use the selected mount once per primitive."""
    previous_tip_tracking = does_tip_tracking()
    previous_volume_tracking = does_volume_tracking()
    set_tip_tracking(True)
    set_volume_tracking(True)
    try:
      for model, volume, flow, rack_factory in (
        ("p20_multi_gen2", 1, 7.6, opentrons_96_filtertiprack_20ul),
        ("p20_multi_gen2", 20, 7.6, opentrons_96_filtertiprack_20ul),
        ("p300_multi_gen2", 20, 94, opentrons_96_tiprack_300ul),
        ("p300_multi_gen2", 300, 94, opentrons_96_tiprack_300ul),
      ):
        for mount in ("left", "right"):
          with self.subTest(model=model, mount=mount, volume=volume):
            backend = OpentronsOT2ChatterboxBackend(
              left_pipette_name=model if mount == "left" else None,
              right_pipette_name=model if mount == "right" else None,
              fixed_head_mount=mount,
              verbose=False,
            )
            deck = OTDeck()
            lh = LiquidHandler(backend=backend, deck=deck)
            await lh.setup(skip_home=True)
            self.assertEqual(list(lh.head), list(range(8)))
            tips = rack_factory(name="tips")
            deck.assign_child_at_slot(tips, slot=1)
            plate = celltreat_96_wellplate_350uL_Fb(name="plate")
            deck.assign_child_at_slot(plate, slot=2)
            for well in plate["A1:H1"]:
              well.tracker.set_volume(volume + 10)
            await lh.pick_up_tips(tips["A1:H1"])
            await lh.aspirate(plate["A1:H1"], vols=[volume] * 8, mix=[Mix(volume, 2, flow)] * 8)
            await lh.dispense(plate["A2:H2"], vols=[volume] * 8)
            await lh.discard_tips()

            primitives = [
              (name, args, kwargs)
              for name, args, kwargs in backend.commands
              if name
              in {
                "lh.pick_up_tip",
                "lh.aspirate_in_place",
                "lh.dispense_in_place",
                "lh.drop_tip_in_place",
              }
            ]
            self.assertEqual(
              [name for name, _, _ in primitives],
              [
                "lh.pick_up_tip",
                "lh.aspirate_in_place",
                "lh.dispense_in_place",
                "lh.aspirate_in_place",
                "lh.dispense_in_place",
                "lh.aspirate_in_place",
                "lh.dispense_in_place",
                "lh.drop_tip_in_place",
              ],
            )
            self.assertTrue(
              all(kwargs["pipette_id"] == f"chatterbox-{mount}" for _, _, kwargs in primitives)
            )
            self.assertTrue(
              all(
                kwargs["volume"] == volume and kwargs["flow_rate"] == flow
                for name, _, kwargs in primitives
                if name in {"lh.aspirate_in_place", "lh.dispense_in_place"}
              )
            )
            self.assertEqual([well.tracker.get_used_volume() for well in plate["A1:H1"]], [10] * 8)
            self.assertEqual(
              [well.tracker.get_used_volume() for well in plate["A2:H2"]], [volume] * 8
            )
            self.assertTrue(all(not tracker.has_tip for tracker in lh.head.values()))
            self.assertEqual(backend.serialize()["fixed_head_mount"], mount)
    finally:
      set_tip_tracking(previous_tip_tracking)
      set_volume_tracking(previous_volume_tracking)


if __name__ == "__main__":
  unittest.main()
