import unittest
from unittest.mock import patch

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.opentrons_backend import (
  OpentronsOT2Backend,
)
from pylabrobot.resources import no_volume_tracking
from pylabrobot.resources.celltreat import CellTreat_96_wellplate_350ul_Fb
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


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
    self.plate = CellTreat_96_wellplate_350ul_Fb(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)

  @patch("ot_api.lh.pick_up_tip")
  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  async def test_tip_pick_up(self, mock_add=None, mock_define=None, mock_pick_up_tip=None):
    assert mock_pick_up_tip is not None and mock_define is not None and mock_add is not None
    mock_define.side_effect = _mock_define
    mock_add.side_effect = _mock_add

    def assert_parameters(labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(offset_x, offset_x)
      self.assertEqual(offset_y, offset_y)
      self.assertEqual(offset_z, offset_z)

    mock_pick_up_tip.side_effect = assert_parameters

    await self.lh.pick_up_tips(self.tip_rack["A1"])

  @patch("ot_api.lh.drop_tip")
  async def test_tip_drop(self, mock_drop_tip):
    def assert_parameters(labware_id, well_name, pipette_id, offset_x, offset_y, offset_z):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
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
    self.plate.get_well("A1").tracker.set_liquids([(None, 10)])
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
