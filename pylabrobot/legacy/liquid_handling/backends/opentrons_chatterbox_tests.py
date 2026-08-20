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
from pylabrobot.resources import Coordinate, set_tip_tracking, set_volume_tracking
from pylabrobot.resources.celltreat import CellTreat_96_wellplate_350ul_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


def _command_types(backend: OpentronsOT2ChatterboxBackend):
  return [command_type for command_type, _params in backend.commands]


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

  async def test_a_channel_reads_back_where_the_dry_run_last_moved_it(self):
    """savePosition reports the recorded move, so a dry run can drive a channel."""
    await self.backend.move_channel_to(0, x=50.0, y=60.0, z=70.0)

    self.assertEqual(await self.backend.get_channel_position(0), Coordinate(50.0, 60.0, 70.0))

    await self.backend.move_channel_x(0, x=15.0)

    self.assertEqual(await self.backend.get_channel_position(0), Coordinate(15.0, 60.0, 70.0))

  async def test_full_protocol_sends_one_robot_command_per_operation(self):
    """A pickup -> aspirate -> dispense -> trash-discard puts exactly one command
    of each kind on the wire, via the real backend logic."""
    self.plate.get_well("A1").tracker.set_volume(15)
    await self.lh.pick_up_tips(self.tips["A1"])
    await self.lh.aspirate(self.plate["A1"], vols=[10])
    await self.lh.dispense(self.plate["B1"], vols=[10])
    await self.lh.discard_tips()

    types = _command_types(self.backend)
    self.assertEqual(types.count("pickUpTip"), 1)
    self.assertEqual(types.count("aspirateInPlace"), 1)
    self.assertEqual(types.count("dispenseInPlace"), 1)
    # api_version defaults to 7.1.0, so the discard routes through the trash addressable area
    self.assertEqual(types.count("moveToAddressableAreaForDropTip"), 1)
    self.assertEqual(types.count("dropTipInPlace"), 1)

  async def test_a_pick_up_names_the_well_and_the_pipette_the_robot_needs(self):
    """The backend builds the wire params itself now, so they are pinned here."""
    await self.lh.pick_up_tips(self.tips["A1"])

    params = dict(self.backend.commands)["pickUpTip"]
    self.assertEqual(params["labwareId"], self.backend.get_ot_name("tips"))
    tip_spot = self.tips.get_item("A1")
    self.assertEqual(params["wellName"], self.backend.get_ot_name(tip_spot.name))
    assert self.backend.left_pipette is not None
    self.assertEqual(params["pipetteId"], self.backend.left_pipette["pipetteId"])
    self.assertEqual(params["wellLocation"]["origin"], "bottom")

  def test_unknown_pipette_name_raises(self):
    """An unrecognised pipette name is rejected at construction."""
    with self.assertRaises(ValueError):
      OpentronsOT2ChatterboxBackend(left_pipette_name="not_a_pipette")

  def test_the_chatterbox_checks_the_budgets_the_real_backend_checks(self):
    """It skips the real __init__, so the two share one helper rather than drifting."""
    with self.assertRaises(ValueError):
      OpentronsOT2ChatterboxBackend(status_poll_interval=0.0, verbose=False)

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


if __name__ == "__main__":
  unittest.main()
