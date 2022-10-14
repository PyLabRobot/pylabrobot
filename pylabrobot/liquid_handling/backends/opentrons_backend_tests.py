import unittest
from unittest.mock import patch

from pylabrobot.liquid_handling.backends.opentrons_backend import OpentronsBackend
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.resources.opentrons import (
  OTDeck,
  opentrons_96_filtertiprack_20ul,
  usascientific_96_wellplate_2point4ml_deep
)


class OpentronsBackendSetupTests(unittest.TestCase):
  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  def test_setup(self, mock_add_mounted_pipettes, mock_create):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = ("left-pipette-id", "right-pipette-id")

    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    self.lh.setup()


def _mock_define(lw):
  return dict(data=dict(definitionUri=f"lw['namespace']/{lw['metadata']['displayName']}/1"))

def _mock_add(load_name, namespace, slot, version, labware_id, display_name):
  # pylint: disable=unused-argument
  return labware_id


class OpentronsBackendDefinitionTests(unittest.TestCase):
  """ Test for the callback when assigning labware to the deck. """

  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  def setUp(self, mock_add_mounted_pipettes, mock_create):
    mock_create.return_value = "run-id"
    mock_add_mounted_pipettes.return_value = ("left-pipette-id", "right-pipette-id")
    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    self.lh.setup()

  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  def test_assigned_resource_callback(self, mock_add, mock_define):
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.lh.deck.assign_child_resource(self.tip_rack, slot=1)

    self.plate = usascientific_96_wellplate_2point4ml_deep(name="plate")
    self.lh.deck.assign_child_resource(self.plate, slot=11)


class OpentronsBackendCommandTests(unittest.TestCase):
  """ Tests Opentrons commands """

  @patch("ot_api.runs.create")
  @patch("ot_api.lh.add_mounted_pipettes")
  @patch("ot_api.labware.define")
  @patch("ot_api.labware.add")
  def setUp(self, mock_add, mock_define, mock_add_mounted_pipettes, mock_create):
    mock_add.side_effect = _mock_add
    mock_define.side_effect = _mock_define
    mock_add_mounted_pipettes.return_value = (
      dict(pipetteId="left-pipette-id", name="p20_single_gen2"),
      dict(pipetteId="right-pipette-id", name="p20_single_gen2"))
    mock_create.return_value = "run-id"

    self.backend = OpentronsBackend(host="localhost", port=1338)
    self.lh = LiquidHandler(backend=self.backend, deck=OTDeck())
    self.lh.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.lh.deck.assign_child_resource(self.tip_rack, slot=1)
    self.plate = usascientific_96_wellplate_2point4ml_deep(name="plate")
    self.lh.deck.assign_child_resource(self.plate, slot=11)

  @patch("ot_api.lh.pick_up_tip")
  def test_tip_pick_up(self, mock_pick_up_tip=None):
    assert mock_pick_up_tip is not None # just the default for pylint, provided by @patch
    def assert_parameters(labware_id, well_name, pipette_id):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
    mock_pick_up_tip.side_effect = assert_parameters

    self.lh.pick_up_tips(self.tip_rack["A1"])

  @patch("ot_api.lh.drop_tip")
  def test_tip_discard(self, mock_drop_tip):
    def assert_parameters(labware_id, well_name, pipette_id):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
    mock_drop_tip.side_effect = assert_parameters

    self.test_tip_pick_up()
    self.lh.discard_tips(self.tip_rack["A1"])

  @patch("ot_api.lh.aspirate")
  def test_aspirate(self, mock_aspirate):
    def assert_parameters(labware_id, well_name, pipette_id, volume, flow_rate, offset_z):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 3.78)
      self.assertEqual(offset_z, 0)
    mock_aspirate.side_effect = assert_parameters

    self.test_tip_pick_up()
    self.lh.aspirate(self.tip_rack["A1"], vols=[10], liquid_classes=None)

  @patch("ot_api.lh.dispense")
  def test_dispense(self, mock_dispense):
    def assert_parameters(labware_id, well_name, pipette_id, volume, flow_rate, offset_z):
      self.assertEqual(labware_id, "tip_rack")
      self.assertEqual(well_name, "tip_rack_A1")
      self.assertEqual(pipette_id, "left-pipette-id")
      self.assertEqual(volume, 10)
      self.assertEqual(flow_rate, 7.56)
      self.assertEqual(offset_z, 0)
    mock_dispense.side_effect = assert_parameters

    self.test_tip_pick_up()
    self.lh.dispense(self.tip_rack["A1"], vols=[10], liquid_classes=None)

  def test_pick_up_tips96(self):
    with self.assertRaises(NotImplementedError):
      self.lh.pick_up_tips96(self.plate)

  def test_discard_tips96(self):
    with self.assertRaises(NotImplementedError):
      self.lh.discard_tips96(self.plate)

  def test_aspirate96(self):
    with self.assertRaises(NotImplementedError):
      self.lh.aspirate_plate(self.plate, volume=100)

  def test_dispense96(self):
    with self.assertRaises(NotImplementedError):
      self.lh.dispense_plate(self.plate, volume=100)


if __name__ == "__main__":
  unittest.main()
