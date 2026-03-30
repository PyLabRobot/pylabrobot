"""Tests for the OT-2 Device/Driver/PIPBackend architecture."""

import unittest

from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.device import Device
from pylabrobot.opentrons.ot2.pip_backend import OpentronsOT2PIPBackend
from pylabrobot.opentrons.ot2.simulator import (
  OpentronsOT2SimulatorDriver,
  OpentronsOT2SimulatorPIPBackend,
)
from pylabrobot.resources import Coordinate, Tip
from pylabrobot.resources.opentrons import OTDeck, opentrons_96_filtertiprack_20ul


class TestSimulatorDriverLifecycle(unittest.IsolatedAsyncioTestCase):

  async def test_setup_initializes_pipettes(self):
    driver = OpentronsOT2SimulatorDriver()
    await driver.setup()
    self.assertIsNotNone(driver.left_pipette)
    self.assertIsNotNone(driver.right_pipette)
    self.assertEqual(driver.left_pipette["name"], "p300_single_gen2")
    self.assertEqual(driver.right_pipette["name"], "p20_single_gen2")

  async def test_setup_with_none_pipettes(self):
    driver = OpentronsOT2SimulatorDriver(left_pipette_name=None, right_pipette_name=None)
    await driver.setup()
    self.assertIsNone(driver.left_pipette)
    self.assertIsNone(driver.right_pipette)

  async def test_stop_clears_pipettes(self):
    driver = OpentronsOT2SimulatorDriver()
    await driver.setup()
    await driver.stop()
    self.assertIsNone(driver.left_pipette)

  def test_invalid_pipette_name_raises(self):
    with self.assertRaises(ValueError):
      OpentronsOT2SimulatorDriver(left_pipette_name="invalid_pipette")

  def test_serialize(self):
    driver = OpentronsOT2SimulatorDriver()
    s = driver.serialize()
    self.assertEqual(s["type"], "OpentronsOT2SimulatorDriver")
    self.assertEqual(s["left_pipette_name"], "p300_single_gen2")
    self.assertEqual(s["right_pipette_name"], "p20_single_gen2")


class TestSimulatorDriverWireMethods(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver()
    await self.driver.setup()

  async def asyncTearDown(self):
    await self.driver.stop()

  async def test_move_arm_tracks_position(self):
    self.driver.move_arm(pipette_id="sim-left", location_x=10, location_y=20, location_z=30)
    pos = self.driver.save_position("sim-left")
    result = pos["data"]["result"]["position"]
    self.assertAlmostEqual(result["x"], 10.0)
    self.assertAlmostEqual(result["y"], 20.0)
    self.assertAlmostEqual(result["z"], 30.0)

  async def test_define_labware_returns_valid_uri(self):
    result = self.driver.define_labware({"metadata": {"displayName": "my_rack"}})
    parts = result["data"]["definitionUri"].split("/")
    self.assertEqual(len(parts), 3)
    self.assertEqual(parts[0], "pylabrobot")

  async def test_list_connected_modules_empty(self):
    self.assertEqual(await self.driver.list_connected_modules(), [])


class TestSimulatorPIPBackend(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver(
      left_pipette_name="p300_single_gen2",
      right_pipette_name="p20_single_gen2",
    )
    await self.driver.setup()
    self.backend = OpentronsOT2SimulatorPIPBackend(self.driver)
    self.deck = OTDeck()
    self.backend.set_deck(self.deck)
    await self.backend._on_setup()
    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)

  async def asyncTearDown(self):
    await self.backend._on_stop()
    await self.driver.stop()

  def test_num_channels(self):
    self.assertEqual(self.backend.num_channels, 2)

  def test_can_pick_up_tip_20ul(self):
    tip = Tip(has_filter=True, total_tip_length=39.2, maximal_volume=20, fitting_depth=8.25, name="t")
    self.assertTrue(self.backend.can_pick_up_tip(1, tip))   # right: p20
    self.assertFalse(self.backend.can_pick_up_tip(0, tip))  # left: p300

  def test_can_pick_up_tip_300ul(self):
    tip = Tip(has_filter=False, total_tip_length=51.0, maximal_volume=300, fitting_depth=8.0, name="t")
    self.assertTrue(self.backend.can_pick_up_tip(0, tip))   # left: p300
    self.assertFalse(self.backend.can_pick_up_tip(1, tip))  # right: p20

  async def test_pick_up_and_drop_tip_state(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    ops = [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)]
    await self.backend.pick_up_tips(ops, use_channels=[1])
    self.assertTrue(self.backend.right_pipette_has_tip)
    self.assertFalse(self.backend.left_pipette_has_tip)

    ops = [TipDrop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)]
    await self.backend.drop_tips(ops, use_channels=[1])
    self.assertFalse(self.backend.right_pipette_has_tip)

  def test_select_liquid_pipette_prefers_left(self):
    self.backend.left_pipette_has_tip = True
    self.backend.right_pipette_has_tip = True
    self.assertEqual(self.backend.select_liquid_pipette(100), "sim-left")

  def test_select_liquid_pipette_no_tip_returns_none(self):
    self.assertIsNone(self.backend.select_liquid_pipette(100))

  def test_get_ot_name_stable(self):
    self.assertEqual(self.backend.get_ot_name("r"), self.backend.get_ot_name("r"))

  def test_get_ot_name_unique(self):
    self.assertNotEqual(self.backend.get_ot_name("a"), self.backend.get_ot_name("b"))

  async def test_on_stop_clears_state(self):
    self.backend._plr_name_to_load_name["foo"] = "bar"
    self.backend._tip_racks["rack"] = 1
    self.backend.left_pipette_has_tip = True
    await self.backend._on_stop()
    self.assertEqual(self.backend._plr_name_to_load_name, {})
    self.assertEqual(self.backend._tip_racks, {})
    self.assertFalse(self.backend.left_pipette_has_tip)

  def test_set_tip_state_unknown_pipette_raises(self):
    with self.assertRaises(ValueError):
      self.backend._set_tip_state("nonexistent-id", True)

  def test_deck_not_set_raises(self):
    driver = OpentronsOT2SimulatorDriver()
    driver._init_pipettes()
    backend = OpentronsOT2SimulatorPIPBackend(driver)
    with self.assertRaises(AssertionError):
      _ = backend.deck


class TestDeviceIntegration(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver()
    self.deck = OTDeck()
    self.backend = OpentronsOT2SimulatorPIPBackend(self.driver)
    self.backend.set_deck(self.deck)
    self.cap = PIP(backend=self.backend)

    self.device = Device.__new__(Device)
    self.device._driver = self.driver
    self.device._capabilities = [self.cap]
    self.device._setup_finished = False
    await self.device.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)

  async def asyncTearDown(self):
    if self.device.setup_finished:
      await self.device.stop()

  async def test_setup_finished(self):
    self.assertTrue(self.device.setup_finished)
    self.assertTrue(self.cap._setup_finished)

  async def test_stop_clears_setup(self):
    await self.device.stop()
    self.assertFalse(self.device.setup_finished)
    self.assertFalse(self.cap._setup_finished)

  async def test_pick_up_tips_through_capability(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    ops = [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)]
    await self.cap.backend.pick_up_tips(ops, use_channels=[1])
    self.assertTrue(self.backend.right_pipette_has_tip)

  async def test_context_manager(self):
    driver = OpentronsOT2SimulatorDriver()
    device = Device.__new__(Device)
    device._driver = driver
    device._capabilities = []
    device._setup_finished = False

    async with device:
      self.assertTrue(device.setup_finished)
    self.assertFalse(device.setup_finished)


class TestSerializationRoundTrip(unittest.TestCase):

  def test_simulator_driver_serialize(self):
    driver = OpentronsOT2SimulatorDriver(left_pipette_name="p1000_single_gen2", right_pipette_name=None)
    self.assertEqual(driver.serialize(), {
      "type": "OpentronsOT2SimulatorDriver",
      "left_pipette_name": "p1000_single_gen2",
      "right_pipette_name": None,
    })

  def test_ot2_device_deserialize_structure(self):
    from pylabrobot.opentrons.ot2.ot2 import OpentronsOT2
    data = {"type": "OpentronsOT2", "host": "192.168.1.100", "port": 31950}
    data_copy = data.copy()
    data_copy.pop("type")
    self.assertEqual(data_copy, {"host": "192.168.1.100", "port": 31950})
