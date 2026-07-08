import unittest
from unittest.mock import patch

import pytest

pytest.importorskip("ot_api")

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.opentrons_backend import (
  _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
  OpentronsOT2Backend,
)
from pylabrobot.liquid_handling.errors import NoChannelError
from pylabrobot.liquid_handling.standard import (
  Drop,
  Pickup,
  SingleChannelAspiration,
)
from pylabrobot.resources import Coordinate, Tip, no_volume_tracking
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul
from pylabrobot.resources.well import Well


def _mock_define(lw):
  return {"data": {"definitionUri": f'lw["namespace"]/{lw["metadata"]["displayName"]}/1'}}


def _mock_add(load_name, namespace, ot_location, version, labware_id, display_name):
  return labware_id


def _mock_health_get():
  return {
    "api_version": "7.0.1",
  }


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
    self.backend.ot_api_version = _OT_DECK_IS_ADDRESSABLE_AREA_VERSION

    await self.lh.pick_up_tips(self.tip_rack["A1"])
    await self.lh.discard_tips()

    mock_to_trash.assert_called_once()
    mock_drop_in_place.assert_called_once()
    mock_drop_tip.assert_not_called()


def _make_backend_with_pipettes(left_name="p300_single_gen2", right_name="p20_single_gen2"):
  """Create a backend with pipette state set directly (no ot_api needed)."""
  backend = OpentronsOT2Backend.__new__(OpentronsOT2Backend)
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
      total_tip_length=39.2,
      maximal_volume=20,
      fitting_depth=8.25,
      name="test_tip_20",
    )
    self.tip_300 = Tip(
      has_filter=False,
      total_tip_length=51.0,
      maximal_volume=300,
      fitting_depth=8.0,
      name="test_tip_300",
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
