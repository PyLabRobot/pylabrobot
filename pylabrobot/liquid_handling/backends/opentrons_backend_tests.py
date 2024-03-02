import sys
import unittest
from unittest.mock import patch

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.opentrons_backend import OpentronsBackend
from pylabrobot.resources import no_volume_tracking
from pylabrobot.resources.opentrons import (
  OTDeck,
  opentrons_96_filtertiprack_20ul,
  usascientific_96_wellplate_2point4ml_deep,
)


@unittest.skipIf(sys.version_info >= (3, 11), "requires Python 3.10 or lower")
class OpentronsBackendSetupTests(unittest.IsolatedAsyncioTestCase):
  """ Tests for setup and stop """
  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.add")
  @patch("ot_api.labware.define")
  async def test_setup(self, mock_define, mock_add, mock_add_mounted_pipettes, mock_create):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = ("left-pipette-id", "right-pipette-id")
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define

    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    await self.lh.setup()

  def test_serialize(self):
    serialized = OpentronsBackend(host="localhost", port=1337).serialize()
    self.assertEqual(serialized, {"type": "OpentronsBackend", "host": "localhost", "port": 1337})
    self.assertEqual(OpentronsBackend.deserialize(serialized).__class__.__name__,
      "OpentronsBackend")


def _mock_define(lw):
  return {
    "data": {
      "definitionUri": f"lw['namespace']/{lw['metadata']['displayName']}/1"
    }
  }

def _mock_add(load_name, namespace, slot, version, labware_id, display_name):
  # pylint: disable=unused-argument
  return labware_id


@unittest.skipIf(sys.version_info >= (3, 11), "requires Python 3.10 or lower")
class OpentronsBackendDefinitionTests(unittest.IsolatedAsyncioTestCase):
  """ Test for the callback when assigning labware to the deck. """

  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.add")
  @patch("ot_api.labware.define")
  async def asyncSetUp(self, mock_define, mock_add, mock_add_mounted_pipettes, mock_create):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = ("left-pipette-id", "right-pipette-id")
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define

    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  def test_assigned_resource_callback(self, mock_add, mock_define):
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)

    self.plate = usascientific_96_wellplate_2point4ml_deep(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)


@unittest.skipIf(sys.version_info >= (3, 11), "requires Python 3.10 or lower")
class OpentronsBackendCommandTests(unittest.IsolatedAsyncioTestCase):
  """ Tests Opentrons commands """

  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  async def asyncSetUp(self, mock_add, mock_define, mock_add_mounted_pipettes, mock_create):
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define
    mock_add_mounted_pipettes.return_value = (
      {"pipetteId": "left-pipette-id", "name": "p20_single_gen2"},
      {"pipetteId": "right-pipette-id", "name": "p20_single_gen2"})
    mock_create.return_value = "run-id"

    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.deck = OTDeck()
    self.lh = LiquidHandler(backend=self.backend, deck=self.deck)
    await self.lh.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)
    self.plate = usascientific_96_wellplate_2point4ml_deep(name="plate")
    self.deck.assign_child_at_slot(self.plate, slot=11)

  @patch("ot_api.lh.pick_up_tip")
  async def test_tip_pick_up(self, mock_pick_up_tip=None):
    assert mock_pick_up_tip is not None # just the default for pylint, provided by @patch
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

  @patch("ot_api.lh.aspirate")
  async def test_aspirate(self, mock_aspirate=None):
    assert mock_aspirate is not None # just the default for pylint, provided by @patch
    def assert_parameters(labware_id, well_name, pipette_id, volume, flow_rate,
      offset_x, offset_y, offset_z):
      self.assertEqual(labware_id, "plate")
      self.assertEqual(well_name, "plate_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 3.78)
      self.assertEqual(offset_x, 0)
      self.assertEqual(offset_y, 0)
      self.assertEqual(offset_z, 0)
    mock_aspirate.side_effect = assert_parameters

    await self.test_tip_pick_up()
    self.plate.get_well("A1").tracker.set_liquids([(None, 10)])
    await self.lh.aspirate(self.plate["A1"], vols=[10])

  @patch("ot_api.lh.dispense")
  async def test_dispense(self, mock_dispense):
    def assert_parameters(labware_id, well_name, pipette_id, volume, flow_rate,
      offset_x, offset_y, offset_z):
      self.assertEqual(labware_id, "plate")
      self.assertEqual(well_name, "plate_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 7.56)
      self.assertEqual(offset_x, 0)
      self.assertEqual(offset_y, 0)
      self.assertEqual(offset_z, 0)
    mock_dispense.side_effect = assert_parameters

    await self.test_aspirate() # aspirate first
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[10])

  async def test_pick_up_tips96(self):
    with self.assertRaises(NotImplementedError):
      await self.lh.pick_up_tips96(self.tip_rack)
