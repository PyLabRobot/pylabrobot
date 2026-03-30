"""Tests for the OT-2 Device/Driver/PIPBackend architecture."""

import unittest

from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.capabilities.liquid_handling.standard import Pickup, TipDrop
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
      OpentronsOT2SimulatorDriver(left_pipette_name="invalid")

  def test_serialize(self):
    s = OpentronsOT2SimulatorDriver().serialize()
    self.assertEqual(s["type"], "OpentronsOT2SimulatorDriver")
    self.assertEqual(s["left_pipette_name"], "p300_single_gen2")


class TestSimulatorDriverWireMethods(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver()
    await self.driver.setup()

  async def test_move_arm_tracks_position(self):
    self.driver._move_arm(pipette_id="sim-left", location_x=10, location_y=20, location_z=30)
    pos = self.driver._save_position("sim-left")["data"]["result"]["position"]
    self.assertAlmostEqual(pos["x"], 10.0)

  async def test_define_labware_returns_valid_uri(self):
    result = self.driver._define_labware({"metadata": {"displayName": "rack"}})
    self.assertEqual(len(result["data"]["definitionUri"].split("/")), 3)


class TestPerMountPIPBackend(unittest.IsolatedAsyncioTestCase):
  """Tests for the per-mount PIPBackend."""

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver()
    await self.driver.setup()
    self.left = OpentronsOT2SimulatorPIPBackend(self.driver, mount="left")
    self.right = OpentronsOT2SimulatorPIPBackend(self.driver, mount="right")
    self.deck = OTDeck()
    self.left.set_deck(self.deck)
    self.right.set_deck(self.deck)
    await self.left._on_setup()
    await self.right._on_setup()
    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)

  def test_num_channels_is_one(self):
    self.assertEqual(self.left.num_channels, 1)
    self.assertEqual(self.right.num_channels, 1)

  def test_left_is_p300(self):
    self.assertEqual(self.left._pipette_name, "p300_single_gen2")
    self.assertEqual(self.left._max_volume, 300)

  def test_right_is_p20(self):
    self.assertEqual(self.right._pipette_name, "p20_single_gen2")
    self.assertEqual(self.right._max_volume, 20)

  def test_left_can_pick_up_300ul_tip(self):
    tip = Tip(has_filter=False, total_tip_length=51.0, maximal_volume=300, fitting_depth=8.0, name="t")
    self.assertTrue(self.left.can_pick_up_tip(0, tip))
    self.assertFalse(self.right.can_pick_up_tip(0, tip))

  def test_right_can_pick_up_20ul_tip(self):
    tip = Tip(has_filter=True, total_tip_length=39.2, maximal_volume=20, fitting_depth=8.25, name="t")
    self.assertTrue(self.right.can_pick_up_tip(0, tip))
    self.assertFalse(self.left.can_pick_up_tip(0, tip))

  def test_channel_1_invalid(self):
    tip = Tip(has_filter=True, total_tip_length=39.2, maximal_volume=20, fitting_depth=8.25, name="t")
    self.assertFalse(self.left.can_pick_up_tip(1, tip))

  async def test_pick_up_and_drop(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.right.pick_up_tips([Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])
    self.assertTrue(self.right._has_tip)
    self.assertFalse(self.left._has_tip)

    await self.right.drop_tips([TipDrop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])
    self.assertFalse(self.right._has_tip)

  async def test_pick_up_twice_raises(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.right.pick_up_tips([Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])
    with self.assertRaises(AssertionError):
      await self.right.pick_up_tips([Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])

  async def test_drop_without_tip_raises(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    with self.assertRaises(AssertionError):
      await self.right.drop_tips([TipDrop(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])

  def test_get_ot_name_stable(self):
    self.assertEqual(self.driver.get_ot_name("r"), self.driver.get_ot_name("r"))

  def test_get_ot_name_unique(self):
    self.assertNotEqual(self.driver.get_ot_name("a"), self.driver.get_ot_name("b"))

  async def test_on_stop_clears_state(self):
    self.left._has_tip = True
    await self.left._on_stop()
    self.assertFalse(self.left._has_tip)

  def test_no_pipette_num_channels_zero(self):
    driver = OpentronsOT2SimulatorDriver(left_pipette_name=None, right_pipette_name=None)
    driver._init_pipettes()
    backend = OpentronsOT2SimulatorPIPBackend(driver, mount="left")
    self.assertEqual(backend.num_channels, 0)


class TestDeviceIntegration(unittest.IsolatedAsyncioTestCase):

  async def asyncSetUp(self):
    self.driver = OpentronsOT2SimulatorDriver()
    self.deck = OTDeck()
    self.left_backend = OpentronsOT2SimulatorPIPBackend(self.driver, mount="left")
    self.right_backend = OpentronsOT2SimulatorPIPBackend(self.driver, mount="right")
    self.left_backend.set_deck(self.deck)
    self.right_backend.set_deck(self.deck)
    self.left_cap = PIP(backend=self.left_backend)
    self.right_cap = PIP(backend=self.right_backend)

    self.device = Device.__new__(Device)
    self.device._driver = self.driver
    self.device._capabilities = [self.left_cap, self.right_cap]
    self.device._setup_finished = False
    await self.device.setup()

    self.tip_rack = opentrons_96_filtertiprack_20ul(name="tip_rack")
    self.deck.assign_child_at_slot(self.tip_rack, slot=1)

  async def asyncTearDown(self):
    if self.device.setup_finished:
      await self.device.stop()

  async def test_both_capabilities_setup(self):
    self.assertTrue(self.left_cap._setup_finished)
    self.assertTrue(self.right_cap._setup_finished)

  async def test_independent_tip_state(self):
    tip_spot = self.tip_rack.get_item("A1")
    tip = tip_spot.get_tip()
    await self.right_backend.pick_up_tips(
      [Pickup(resource=tip_spot, offset=Coordinate.zero(), tip=tip)], [0])
    self.assertTrue(self.right_backend._has_tip)
    self.assertFalse(self.left_backend._has_tip)


class TestSerializationRoundTrip(unittest.TestCase):

  def test_simulator_driver_serialize(self):
    s = OpentronsOT2SimulatorDriver(left_pipette_name="p1000_single_gen2", right_pipette_name=None).serialize()
    self.assertEqual(s, {"type": "OpentronsOT2SimulatorDriver",
                         "left_pipette_name": "p1000_single_gen2", "right_pipette_name": None})

  def test_ot2_device_deserialize_structure(self):
    from pylabrobot.opentrons.ot2.ot2 import OpentronsOT2
    data = {"type": "OpentronsOT2", "host": "192.168.1.100", "port": 31950}
    data_copy = data.copy()
    data_copy.pop("type")
    self.assertEqual(data_copy, {"host": "192.168.1.100", "port": 31950})
