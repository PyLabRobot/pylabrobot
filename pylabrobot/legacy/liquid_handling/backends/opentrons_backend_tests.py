import unittest
from dataclasses import replace
from unittest.mock import patch

import pytest

pytest.importorskip("ot_api")

from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import (
  OpentronsOT2Backend,
  _fixed_trash_is_addressable,
)
from pylabrobot.legacy.liquid_handling.errors import NoChannelError
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  Mix,
  Pickup,
  SingleChannelAspiration,
)
from pylabrobot.resources import (
  Coordinate,
  Tip,
  does_tip_tracking,
  does_volume_tracking,
  no_volume_tracking,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.errors import HasTipError, TooLittleLiquidError, TooLittleVolumeError
from pylabrobot.resources.opentrons import (
  OTDeck,
  opentrons_96_filtertiprack_20ul,
  opentrons_96_tiprack_300ul,
)
from pylabrobot.resources.well import Well


def _mock_define(lw):
  return {"data": {"definitionUri": f'lw["namespace"]/{lw["metadata"]["displayName"]}/1'}}


def _mock_add(load_name, namespace, ot_location, version, labware_id, display_name):
  return labware_id


def _mock_health_get():
  return {
    "api_version": "7.0.1",
  }


@pytest.mark.parametrize(
  ("version", "expected"),
  (
    ("7.0.1", False),
    ("7.1.0", True),
    ("9.1.0-alpha.12", True),
    ("9.1.0.dev12", True),
    ("26.6.0", True),
  ),
)
def test_fixed_trash_is_addressable(version: str, expected: bool) -> None:
  assert _fixed_trash_is_addressable(version) is expected


def test_fixed_trash_rejects_invalid_server_version() -> None:
  with pytest.raises(ValueError, match="must start with major, minor, and patch numbers"):
    _fixed_trash_is_addressable("development")


class OpentronsBackendSetupTests(unittest.IsolatedAsyncioTestCase):
  """Tests for setup and stop"""

  @patch("ot_api.runs.create")
  @patch("ot_api.health.home")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.add")
  @patch("ot_api.labware.define")
  @patch("ot_api.health.get")
  async def test_setup(
    self,
    mock_health_get,
    mock_define,
    mock_add,
    mock_add_mounted_pipettes,
    mock_home,
    mock_create,
  ):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = (
      {"pipetteId": "left-pipette-id", "name": "p20_single_gen2"},
      {"pipetteId": "right-pipette-id", "name": "p20_single_gen2"},
    )
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define
    mock_health_get.side_effect = _mock_health_get

    self.backend = OpentronsOT2Backend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    await self.lh.setup()

  def test_serialize(self):
    serialized = OpentronsOT2Backend(host="localhost", port=1337).serialize()
    self.assertEqual(
      serialized,
      {"type": "OpentronsOT2Backend", "host": "localhost", "port": 1337},
    )
    self.assertEqual(
      OpentronsOT2Backend.deserialize(serialized).__class__.__name__,
      "OpentronsOT2Backend",
    )


class OpentronsBackendCommandTests(unittest.IsolatedAsyncioTestCase):
  """Tests Opentrons commands"""

  @patch("ot_api.runs.create")
  @patch("ot_api.health.home")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.add")
  @patch("ot_api.labware.define")
  @patch("ot_api.health.get")
  async def asyncSetUp(
    self,
    mock_health_get,
    mock_define,
    mock_add,
    mock_add_mounted_pipettes,
    mock_home,
    mock_create,
  ):
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define
    mock_add_mounted_pipettes.return_value = (
      {"pipetteId": "left-pipette-id", "name": "p20_single_gen2"},
      {"pipetteId": "right-pipette-id", "name": "p20_single_gen2"},
    )
    mock_create.return_value = "run-id"
    mock_health_get.side_effect = _mock_health_get

    self.backend = OpentronsOT2Backend(host="localhost", port=1338)
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)

  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  async def test_tip_pick_up(self, mock_add=None, mock_define=None, mock_pick_up_tip=None):
    assert mock_pick_up_tip is not None and mock_define is not None and mock_add is not None
    mock_define.side_effect = _mock_define
    mock_add.side_effect = _mock_add

    def assert_parameters(labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
      self.assertEqual(labware_id, self.backend.get_ot_name("tip_rack"))
      self.assertEqual(well_name, self.backend.get_ot_name("tip_rack_A1"))
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(offset_x, offset_x)
      self.assertEqual(offset_y, offset_y)
      self.assertEqual(offset_z, offset_z)

    mock_pick_up_tip.side_effect = assert_parameters

    await self.lh.pick_up_tips(self.tip_rack["A1"])

  @patch("ot_api.lh.drop_tip")
  async def test_tip_drop(self, mock_drop_tip):
    def assert_parameters(labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
      self.assertEqual(well_name, self.backend.get_ot_name("tip_rack_A1"))
      self.assertEqual(well_name, self.backend.get_ot_name("tip_rack_A1"))
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(offset_x, offset_x)
      self.assertEqual(offset_y, offset_y)
      self.assertEqual(offset_z, offset_z)

    mock_drop_tip.side_effect = assert_parameters
    self.backend.ot_api_version = "development"

    await self.test_tip_pick_up()
    await self.lh.drop_tips(self.tip_rack["A1"])

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  async def test_aspirate(self, mock_move=None, mock_aspirate=None):
    assert mock_aspirate is not None and mock_move is not None

    def assert_parameters(
      volume,
      flow_rate,
      pipette_id,
    ):
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 3.78)

    mock_aspirate.side_effect = assert_parameters

    await self.test_tip_pick_up()
    self.plate.get_well("A1").tracker.set_volume(10)
    await self.lh.aspirate(self.plate["A1"], vols=[10])

  @patch("ot_api.lh.dispense_in_place")
  @patch("ot_api.lh.move_arm")
  async def test_dispense(self, mock_move, mock_dispense):
    def assert_parameters(
      volume,
      flow_rate,
      pipette_id,
    ):
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 7.56)

    mock_dispense.side_effect = assert_parameters

    await self.test_aspirate()  # aspirate first
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[10])

  # -- characterization of the remaining ot_api call sites (Phase 0 safety net) --

  @patch("ot_api.health.home")
  async def test_home_calls_health_home(self, mock_home):
    """home() issues exactly one ot_api.health.home() call."""
    await self.backend.home()
    mock_home.assert_called_once_with()

  @patch("ot_api.modules.list_connected_modules")
  async def test_list_connected_modules_passthrough(self, mock_modules):
    """list_connected_modules() returns ot_api.modules.list_connected_modules() verbatim."""
    mock_modules.return_value = [{"id": "tempdeck"}]
    result = await self.backend.list_connected_modules()
    mock_modules.assert_called_once_with()
    self.assertEqual(result, [{"id": "tempdeck"}])

  @patch("ot_api.run_id", "run-id", create=True)
  @patch("ot_api.requestor.post")
  async def test_stop_cancels_active_run_and_clears_pipettes(self, mock_post):
    """stop() cancels the active run through the requestor and clears mounted pipettes."""
    await self.backend.stop()
    mock_post.assert_called_once_with("/runs/run-id/cancel")
    self.assertIsNone(self.backend.left_pipette)
    self.assertIsNone(self.backend.right_pipette)

  @patch("ot_api.lh.drop_tip_in_place")
  @patch("ot_api.lh.move_to_addressable_area_for_drop_tip")
  @patch("ot_api.lh.drop_tip")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  async def test_tip_drop_to_trash_uses_addressable_area(
    self,
    mock_add,
    mock_define,
    mock_pick_up_tip,
    mock_drop_tip,
    mock_to_trash,
    mock_drop_in_place,
  ):
    """At api_version >= 7.1.0 a discard to the deck trash routes via the addressable
    area (move_to_addressable_area_for_drop_tip + drop_tip_in_place), not drop_tip."""
    mock_define.side_effect = _mock_define
    mock_add.side_effect = _mock_add
    self.backend.ot_api_version = "26.6.0"

    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.discard_tips()

    mock_to_trash.assert_called_once()
    mock_drop_in_place.assert_called_once()
    mock_drop_tip.assert_not_called()


class OpentronsFixedHeadTests(unittest.IsolatedAsyncioTestCase):
  """Tests for supported OT-2 GEN2 eight-channel full-head dispatch."""

  @patch("ot_api.runs.create", return_value="run-id")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.health.get", side_effect=_mock_health_get)
  async def asyncSetUp(self, _mock_health, mock_pipettes, _mock_create):
    """Build a full head and a deck with eight confirmed source volumes."""
    self._previous_volume_tracking = does_volume_tracking()
    self._previous_tip_tracking = does_tip_tracking()
    set_volume_tracking(True)
    set_tip_tracking(True)
    mock_pipettes.return_value = (
      {"pipetteId": "fixed-head-id", "name": "p300_multi_gen2"},
      {"pipetteId": "right-id", "name": "p20_single_gen2"},
    )
    self.backend = OpentronsOT2Backend(host="localhost", port=1338, fixed_head_mount="left")
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup(skip_home=True)
    self.tip_rack = opentrons_96_tiprack_300ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.plate = celltreat_96_wellplate_350uL_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)
    for well in self.plate["A1:H1"]:
      well.tracker.set_volume(100)

  async def asyncTearDown(self):
    """Restore the caller's tip- and volume-tracking settings."""
    set_volume_tracking(self._previous_volume_tracking)
    set_tip_tracking(self._previous_tip_tracking)

  @patch("ot_api.lh.drop_tip")
  @patch("ot_api.lh.dispense_in_place")
  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_full_head_uses_one_dispatch_per_primitive(
    self,
    _mock_define,
    _mock_add,
    mock_pick_up,
    _mock_move,
    mock_aspirate,
    mock_dispense,
    mock_drop,
  ):
    """Transfer eight per-nozzle volumes and return eight tips through one command each."""
    self.assertEqual(len({spot.get_tip().name for spot in self.tip_rack["A1:H1"]}), 8)
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    await self.lh.aspirate(self.plate["A1:H1"], vols=[20] * 8)
    self.assertEqual([well.tracker.get_used_volume() for well in self.plate["A1:H1"]], [80] * 8)
    self.assertEqual(
      [tracker.get_tip().tracker.get_used_volume() for tracker in self.lh.head.values()], [20] * 8
    )
    await self.lh.dispense(self.plate["A2:H2"], vols=[20] * 8)
    self.assertEqual([well.tracker.get_used_volume() for well in self.plate["A2:H2"]], [20] * 8)
    await self.lh.return_tips()

    mock_pick_up.assert_called_once()
    self.assertEqual(
      mock_pick_up.call_args.kwargs["well_name"], self.backend.get_ot_name("tip_rack_A1")
    )
    mock_aspirate.assert_called_once_with(
      volume=20.0,
      flow_rate=94,
      pipette_id="fixed-head-id",
    )
    mock_dispense.assert_called_once_with(
      volume=20.0,
      flow_rate=94,
      pipette_id="fixed-head-id",
    )
    mock_drop.assert_called_once()
    self.assertEqual(
      mock_drop.call_args.kwargs["well_name"], self.backend.get_ot_name("tip_rack_A1")
    )
    self.assertTrue(all(not tracker.has_tip for tracker in self.lh.head.values()))
    self.assertTrue(all(spot.has_tip() for spot in self.tip_rack["A1:H1"]))

  @patch("ot_api.lh.pick_up_tip")
  async def test_fixed_head_rejects_different_tip_geometry_before_dispatch(self, mock_pick_up):
    """Distinct tip identities are permitted, but every physical property must agree."""
    spots = self.tip_rack["A1:H1"]
    ops = [Pickup(spot, Coordinate.zero(), spot.get_tip()) for spot in spots]
    original = ops[-1].tip
    parameters = dict(
      name="different_tip",
      diameter=original.get_size_x(),
      size_z=original.get_size_z(),
      has_filter=original.has_filter,
      maximal_volume=original.maximal_volume,
      fitting_depth=original.fitting_depth,
      nominal_volume=original.nominal_volume,
    )
    for changes in (
      {"diameter": original.get_size_x() + 1},
      {"size_z": original.get_size_z() + 1},
      {"fitting_depth": original.fitting_depth + 1},
      {"has_filter": not original.has_filter},
      {"maximal_volume": original.maximal_volume - 1},
      {"nominal_volume": original.nominal_volume - 1},
      {"collar_height": 1},
      {"pick_up_location": Coordinate(z=1)},
    ):
      with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "shared tip geometry"):
        await self.backend.pick_up_tips(
          ops[:-1] + [replace(ops[-1], tip=Tip(**{**parameters, **changes}))], list(range(8))
        )
    mock_pick_up.assert_not_called()

  @patch("ot_api.lh.pick_up_tip", side_effect=RuntimeError("dispatch failed"))
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_full_head_pickup_failure_rolls_back_all_tip_state(
    self, _mock_define, _mock_add, mock_pick_up
  ):
    """A transport failure commits none of the eight queued tip changes."""
    with self.assertRaisesRegex(RuntimeError, "dispatch failed"):
      await self.lh.pick_up_tips(self.tip_rack["A1:H1"])

    mock_pick_up.assert_called_once()
    self.assertTrue(all(not tracker.has_tip for tracker in self.lh.head.values()))
    self.assertTrue(all(spot.has_tip() for spot in self.tip_rack["A1:H1"]))

  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_fixed_head_rejects_partial_and_reversed_columns_before_dispatch(
    self, _mock_define, _mock_add, mock_pick_up
  ):
    """Reject partial or reversed nozzle targets without a tip command."""
    with self.assertRaisesRegex(ValueError, "channels 0 through 7"):
      await self.lh.pick_up_tips(self.tip_rack["A1:G1"])
    with self.assertRaisesRegex(ValueError, "ordered A-to-H column"):
      await self.lh.pick_up_tips(list(reversed(self.tip_rack["A1:H1"])))

    mock_pick_up.assert_not_called()

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_fixed_head_rejects_mixed_per_nozzle_volumes_before_liquid_dispatch(
    self, _mock_define, _mock_add, _mock_pick_up, _mock_move, mock_aspirate
  ):
    """Unequal volumes reject the complete action and preserve mounted tips."""
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    with self.assertRaisesRegex(ValueError, "one shared volume"):
      await self.lh.aspirate(self.plate["A1:H1"], vols=[20] * 7 + [21])

    mock_aspirate.assert_not_called()
    self.assertTrue(all(tracker.has_tip for tracker in self.lh.head.values()))

  @patch("ot_api.runs.create", return_value="run-id")
  @patch("ot_api.lh.add_mounted_pipettes")
  async def test_fixed_head_setup_rejects_a_legacy_mounted_model(self, mock_pipettes, _mock_create):
    """Model discovery must match an explicitly supported GEN2 identity."""
    mock_pipettes.return_value = (
      {"pipetteId": "left-id", "name": "p50_multi"},
      None,
    )
    backend = OpentronsOT2Backend("localhost", fixed_head_mount="left")
    liquid_handler = LiquidHandler(backend=backend, deck=OTDeck())

    with self.assertRaisesRegex(NoChannelError, "supported OT-2 GEN2 eight-channel"):
      await liquid_handler.setup(skip_home=True)

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_p20_full_head_uses_its_model_range_and_default_flow_rate(
    self, _mock_define, _mock_add, mock_pick_up, _mock_move, mock_aspirate
  ):
    """P20 uses its per-nozzle minimum and default flow through the normal frontend."""
    self.backend.left_pipette = {"pipetteId": "fixed-head-id", "name": "p20_multi_gen2"}
    tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack_20")
    self.deck.assign_child_at_slot(tip_rack, slot=2)

    await self.lh.pick_up_tips(tip_rack["A1:H1"])
    await self.lh.aspirate(self.plate["A1:H1"], vols=[1] * 8)
    self.assertEqual([well.tracker.get_used_volume() for well in self.plate["A1:H1"]], [99] * 8)

    mock_pick_up.assert_called_once()
    mock_aspirate.assert_called_once_with(
      volume=1.0,
      flow_rate=7.6,
      pipette_id="fixed-head-id",
    )

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_fixed_head_rejects_volume_outside_the_detected_model_range(
    self, _mock_define, _mock_add, _mock_pick_up, _mock_move, mock_aspirate
  ):
    """P300 rejects a volume below its operating minimum before liquid dispatch."""
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])

    with self.assertRaisesRegex(NoChannelError, "20 through 300"):
      await self.lh.aspirate(self.plate["A1:H1"], vols=[19] * 8)

    mock_aspirate.assert_not_called()

  @patch("ot_api.lh.aspirate_in_place", side_effect=RuntimeError("dispatch failed"))
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_full_head_liquid_failure_rolls_back_every_source_and_tip(
    self, _mock_define, _mock_add, _mock_pick_up, _mock_move, mock_aspirate
  ):
    """One failed liquid command leaves all eight software volumes uncommitted."""
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    with self.assertRaisesRegex(RuntimeError, "dispatch failed"):
      await self.lh.aspirate(self.plate["A1:H1"], vols=[20] * 8)
    mock_aspirate.assert_called_once()
    self.assertEqual([well.tracker.get_used_volume() for well in self.plate["A1:H1"]], [100] * 8)
    self.assertEqual(
      [tracker.get_tip().tracker.get_used_volume() for tracker in self.lh.head.values()], [0] * 8
    )

  @patch("ot_api.lh.dispense_in_place")
  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_late_invalid_volume_rolls_back_queued_state_without_dispatch(
    self, _mock_define, _mock_add, _mock_pick_up, mock_move, mock_aspirate, mock_dispense
  ):
    """Reject an invalid eighth well without leaving changes queued on the first seven."""
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    self.plate.get_well("H1").tracker.set_volume(0)
    with self.assertRaises(TooLittleLiquidError):
      await self.lh.aspirate(self.plate["A1:H1"], vols=[20] * 8)
    self.assertEqual(
      [well.tracker.get_used_volume() for well in self.plate["A1:H1"]], [100] * 7 + [0]
    )
    self.assertEqual(
      [tracker.get_tip().tracker.get_used_volume() for tracker in self.lh.head.values()], [0] * 8
    )
    mock_move.assert_not_called()
    mock_aspirate.assert_not_called()

    self.plate.get_well("H1").tracker.set_volume(100)
    await self.lh.aspirate(self.plate["A1:H1"], vols=[20] * 8)
    self.plate.get_well("H2").tracker.set_volume(350)
    mock_move.reset_mock()
    with self.assertRaises(TooLittleVolumeError):
      await self.lh.dispense(self.plate["A2:H2"], vols=[20] * 8)
    self.assertEqual(
      [well.tracker.get_used_volume() for well in self.plate["A2:H2"]], [0] * 7 + [350]
    )
    self.assertEqual(
      [tracker.get_tip().tracker.get_used_volume() for tracker in self.lh.head.values()], [20] * 8
    )
    mock_move.assert_not_called()
    mock_dispense.assert_not_called()

  @patch("ot_api.lh.drop_tip")
  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.add", side_effect=_mock_add)
  @patch("ot_api.labware.define", side_effect=_mock_define)
  async def test_late_occupied_return_spot_rolls_back_every_tip_without_dispatch(
    self, _mock_define, _mock_add, _mock_pick_up, mock_drop
  ):
    """An occupied eighth return spot must preserve all mounted tips."""
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    self.tip_rack.get_item("H1").tracker.add_tip(self.tip_rack.get_item("H2").get_tip())
    with self.assertRaises(HasTipError):
      await self.lh.return_tips()
    self.assertTrue(all(tracker.has_tip for tracker in self.lh.head.values()))
    self.assertEqual([spot.has_tip() for spot in self.tip_rack["A1:H1"]], [False] * 7 + [True])
    mock_drop.assert_not_called()

  @patch("ot_api.lh.pick_up_tip")
  async def test_late_mounted_tip_rejects_pickup_without_leaving_queued_changes(self, mock_pick_up):
    """An occupied eighth channel preserves the first seven empty channels and rack tips."""
    tip = self.tip_rack.get_item("H2").get_tip()
    self.lh.head[7].add_tip(tip)
    with self.assertRaises(HasTipError):
      await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    self.assertEqual([tracker.has_tip for tracker in self.lh.head.values()], [False] * 7 + [True])
    self.assertTrue(all(spot.has_tip() for spot in self.tip_rack["A1:H1"]))
    mock_pick_up.assert_not_called()

  @patch("ot_api.lh.pick_up_tip")
  async def test_fixed_head_rejects_displaced_or_rotated_columns_before_dispatch(
    self, mock_pick_up
  ):
    """Index ordering alone cannot authorize a physically misaligned head."""
    for displacement in (Coordinate(x=1), Coordinate(y=1), Coordinate(z=1)):
      spot = self.tip_rack.get_item("H1")
      original = spot.location
      assert original is not None
      spot.location = original + displacement
      with self.assertRaisesRegex(ValueError, "aligned 9 mm"):
        await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
      spot.location = original
    self.tip_rack.rotate(z=180)
    with self.assertRaisesRegex(ValueError, "unrotated"):
      await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    mock_pick_up.assert_not_called()

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  async def test_fixed_head_rejects_unrepresentable_parameters_before_movement(
    self, mock_move, mock_aspirate
  ):
    """A single physical command cannot encode different settings for each nozzle."""
    ops = [
      SingleChannelAspiration(
        well, Coordinate.zero(), self.tip_rack.get_item("A1").get_tip(), 20, None, None, None, None
      )
      for well in self.plate["A1:H1"]
    ]
    mismatches = (
      {"offset": Coordinate(z=1)},
      {"flow_rate": 1},
      {"liquid_height": 1},
      {"blow_out_air_volume": 1},
      {"mix": Mix(20, 2, 10)},
    )
    for changes in mismatches:
      with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "one shared"):
        await self.backend.aspirate(ops[:-1] + [replace(ops[-1], **changes)], list(range(8)))
    mock_move.assert_not_called()
    mock_aspirate.assert_not_called()

  @patch("ot_api.lh.aspirate_in_place")
  @patch("ot_api.lh.move_arm")
  async def test_fixed_head_rejects_surface_following_before_movement(
    self, mock_move, mock_aspirate
  ):
    """Fixed-head mixing cannot follow the liquid surface."""
    for mix in (
      Mix(20, 2, 10, surface_following_distance=1),
      Mix(20, 2, 10, auto_surface_following=True),
    ):
      ops = [
        SingleChannelAspiration(
          well, Coordinate.zero(), self.tip_rack.get_item("A1").get_tip(), 20, None, None, None, mix
        )
        for well in self.plate["A1:H1"]
      ]
      with self.subTest(mix=mix), self.assertRaisesRegex(ValueError, "surface following"):
        await self.backend.aspirate(ops, list(range(8)))
    mock_move.assert_not_called()
    mock_aspirate.assert_not_called()


def _make_backend_with_pipettes(left_name="p300_single_gen2", right_name="p20_single_gen2"):
  """Create a backend with pipette state set directly (no ot_api needed)."""
  backend = OpentronsOT2Backend.__new__(OpentronsOT2Backend)
  backend.fixed_head_mount = None
  backend.left_pipette = {"name": left_name, "pipetteId": "left-id"} if left_name else None
  backend.right_pipette = {"name": right_name, "pipetteId": "right-id"} if right_name else None
  backend.left_pipette_has_tip = False
  backend.right_pipette_has_tip = False
  return backend


class OpentronsSharedHelperTests(unittest.TestCase):
  """Tests for _get_pickup_pipette, _get_drop_pipette, _get_liquid_pipette, _set_tip_state."""

  def setUp(self):
    self.backend = _make_backend_with_pipettes()
    self.deck = OTDeck()
    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.tip_spot = self.tip_rack.get_item("A1")
    self.tip_20 = Tip(
      has_filter=True,
      maximal_volume=20,
      fitting_depth=8.25,
      name="test_tip_20",
      diameter=self.tip_spot.get_tip().get_size_x(),
      size_z=39.2,
    )
    self.tip_300 = Tip(
      has_filter=False,
      maximal_volume=300,
      fitting_depth=8.0,
      name="test_tip_300",
      diameter=opentrons_96_tiprack_300ul("tip_rack_300").get_tip("A1").get_size_x(),
      size_z=51.0,
    )

  # -- _get_pickup_pipette --

  def test_get_pickup_pipette_selects_right_for_20ul(self):
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    self.assertEqual(self.backend._get_pickup_pipette(ops), "right-id")

  def test_get_pickup_pipette_selects_left_for_300ul(self):
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_300)]
    self.assertEqual(self.backend._get_pickup_pipette(ops), "left-id")

  def test_get_pickup_pipette_raises_when_tip_already_mounted(self):
    self.backend.right_pipette_has_tip = True
    ops = [Pickup(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    with self.assertRaises(NoChannelError):
      self.backend._get_pickup_pipette(ops)

  # -- _deck_to_robot_frame --

  def test_deck_to_robot_frame_maps_slot1_corner_to_robot_origin(self):
    """The deck->robot transform subtracts slot 1's corner, so a deck-frame point at slot 1's
    corner becomes the robot origin and a point offset from it keeps that offset."""
    self.backend.set_deck(self.deck)
    corner = self.deck.slot_locations[0]
    self.assertEqual(self.backend._deck_to_robot_frame(corner), Coordinate(0, 0, 0))
    self.assertEqual(
      self.backend._deck_to_robot_frame(corner + Coordinate(10, 20, 3)),
      Coordinate(10, 20, 3),
    )

  # -- _get_drop_pipette --

  def test_get_drop_pipette_selects_right_for_20ul(self):
    self.backend.right_pipette_has_tip = True
    ops = [Drop(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    self.assertEqual(self.backend._get_drop_pipette(ops), "right-id")

  def test_get_drop_pipette_raises_when_no_tip(self):
    ops = [Drop(resource=self.tip_spot, offset=Coordinate.zero(), tip=self.tip_20)]
    with self.assertRaises(NoChannelError):
      self.backend._get_drop_pipette(ops)

  # -- _get_liquid_pipette --

  def test_get_liquid_pipette_selects_left_for_large_volume(self):
    self.backend.left_pipette_has_tip = True
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_300,
        volume=100,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    self.assertEqual(self.backend._get_liquid_pipette(ops), "left-id")

  def test_get_liquid_pipette_selects_right_for_small_volume(self):
    self.backend.right_pipette_has_tip = True
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_20,
        volume=5,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    self.assertEqual(self.backend._get_liquid_pipette(ops), "right-id")

  def test_get_liquid_pipette_raises_without_tip(self):
    well = Well(name="w", size_x=5, size_y=5, size_z=10, max_volume=350)
    ops = [
      SingleChannelAspiration(
        resource=well,
        offset=Coordinate.zero(),
        tip=self.tip_20,
        volume=5,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
    ]
    with self.assertRaises(NoChannelError):
      self.backend._get_liquid_pipette(ops)

  # -- _set_tip_state --

  def test_set_tip_state_left(self):
    self.backend._set_tip_state("left-id", True)
    self.assertTrue(self.backend.left_pipette_has_tip)
    self.assertFalse(self.backend.right_pipette_has_tip)

  def test_set_tip_state_right(self):
    self.backend._set_tip_state("right-id", True)
    self.assertFalse(self.backend.left_pipette_has_tip)
    self.assertTrue(self.backend.right_pipette_has_tip)
