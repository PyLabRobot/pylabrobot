"""Unit tests for EVOPIPBackend (syringe LiHa)."""

import unittest
from unittest.mock import AsyncMock, call

from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.resources import Coordinate, EVO150Deck
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.plates import Microplate_96_Well
from pylabrobot.resources.tecan.tip_carriers import DiTi_3Pos
from pylabrobot.resources.tecan.tip_racks import DiTi_100ul_Te_MO
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.firmware import LiHa
from pylabrobot.tecan.evo.pip_backend import EVOPIPBackend


class PIPBackendTestBase(unittest.IsolatedAsyncioTestCase):
  """Base class with mocked driver and deck setup."""

  def setUp(self):
    super().setUp()

    self.driver = TecanEVODriver()
    self.driver.send_command = AsyncMock()

    async def mock_send(module, command, params=None, **kwargs):
      if command == "RPX":
        return {"data": [5000]}
      if command == "RPY":
        return {"data": [1500, 90]}
      if command == "RPZ":
        return {"data": [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]}
      if command == "RNT":
        return {"data": [8]}
      return {"data": []}

    self.driver.send_command.side_effect = mock_send

    self.deck = EVO150Deck()
    self.backend = EVOPIPBackend(driver=self.driver, deck=self.deck, diti_count=8)

    # Skip actual setup, set state directly
    self.backend._num_channels = 8
    self.backend._x_range = 9866
    self.backend._y_range = 2833
    self.backend._z_range = 2100
    self.backend.liha = LiHa(self.driver, "C5")

    # Deck layout
    self.tip_carrier = DiTi_3Pos(name="tip_carrier")
    self.tip_carrier[0] = self.tip_rack = DiTi_100ul_Te_MO(name="tips")
    self.deck.assign_child_resource(self.tip_carrier, rails=10)

    self.plate_carrier = MP_3Pos(name="plate_carrier")
    self.plate_carrier[0] = self.plate = Microplate_96_Well(name="plate")
    self.deck.assign_child_resource(self.plate_carrier, rails=20)

    self.driver.send_command.reset_mock()


class ConversionTests(PIPBackendTestBase):
  def test_syringe_steps_per_ul(self):
    self.assertEqual(EVOPIPBackend.STEPS_PER_UL, 3.0)

  def test_syringe_speed_factor(self):
    self.assertEqual(EVOPIPBackend.SPEED_FACTOR, 6.0)


class UtilityTests(PIPBackendTestBase):
  def test_bin_use_channels_single(self):
    self.assertEqual(self.backend._bin_use_channels([0]), 1)

  def test_bin_use_channels_multiple(self):
    self.assertEqual(self.backend._bin_use_channels([0, 2, 4]), 0b10101)

  def test_bin_use_channels_all_eight(self):
    self.assertEqual(self.backend._bin_use_channels(list(range(8))), 255)

  def test_first_valid(self):
    val, idx = self.backend._first_valid([None, None, 42, None])
    self.assertEqual(val, 42)
    self.assertEqual(idx, 2)

  def test_first_valid_none(self):
    val, idx = self.backend._first_valid([None, None])
    self.assertIsNone(val)
    self.assertEqual(idx, -1)

  def test_get_ys_from_plate(self):
    op = Aspiration(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
      volume=25.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    ys = self.backend._get_ys([op])
    # Microplate_96_Well has ~9mm well pitch (int truncation of 8.999... * 10)
    self.assertIn(ys, [89, 90])


class PickUpTipsTests(PIPBackendTestBase):
  async def test_pick_up_sends_correct_commands(self):
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.pick_up_tips([op], use_channels=[0])

    # Should send SHZ, PAA, PVL, SEP, PPR, AGT
    cmd_names = [
      c.kwargs.get("command", c.args[1] if len(c.args) > 1 else "?")
      for c in self.driver.send_command.call_args_list
      if c.kwargs.get("module") == "C5" or (c.args and c.args[0] == "C5")
    ]
    self.assertIn("SHZ", cmd_names)
    self.assertIn("PVL", cmd_names)
    self.assertIn("SEP", cmd_names)
    self.assertIn("PPR", cmd_names)
    self.assertIn("AGT", cmd_names)

  async def test_pick_up_syringe_conversion(self):
    """Verify syringe LiHa uses factor 3/6 for tip pickup air gap."""
    op = Pickup(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.pick_up_tips([op], use_channels=[0])

    # SEP should use speed factor 6: 70 * 6 = 420
    sep_calls = [
      c
      for c in self.driver.send_command.call_args_list
      if c
      == call(module="C5", command="SEP", params=[420, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(sep_calls), 1)

    # PPR should use steps/uL 3: 10 * 3 = 30
    ppr_calls = [
      c
      for c in self.driver.send_command.call_args_list
      if c
      == call(module="C5", command="PPR", params=[30, None, None, None, None, None, None, None])
    ]
    self.assertEqual(len(ppr_calls), 1)


class DropTipsTests(PIPBackendTestBase):
  async def test_drop_sends_ast(self):
    op = TipDrop(
      resource=self.tip_rack.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
    )
    await self.backend.drop_tips([op], use_channels=[0])

    # Should send SHZ, PAA, AST
    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    self.assertIn("AST", cmd_names)


class AspirateTests(PIPBackendTestBase):
  async def test_aspirate_sends_tracking_commands(self):
    op = Aspiration(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
      volume=25.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    await self.backend.aspirate([op], use_channels=[0])

    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    # Core aspirate sequence: SHZ, PAA, PVL, SEP, PPR (airgap), SSZ, SEP, STZ, MTR, SSZ, MAZ, PVL, SEP, PPR (tag)
    self.assertIn("MTR", cmd_names)  # tracking movement
    self.assertIn("MAZ", cmd_names)  # retract


class DispenseTests(PIPBackendTestBase):
  async def test_dispense_sends_tracking_commands(self):
    op = Dispense(
      resource=self.plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=self.tip_rack.get_tip("A1"),
      volume=25.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    await self.backend.dispense([op], use_channels=[0])

    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    self.assertIn("MTR", cmd_names)
    self.assertIn("SPP", cmd_names)  # stop speed


class NumChannelsTests(PIPBackendTestBase):
  def test_num_channels(self):
    self.assertEqual(self.backend.num_channels, 8)

  def test_num_channels_before_setup(self):
    fresh = EVOPIPBackend(driver=self.driver, deck=self.deck)
    with self.assertRaises(RuntimeError):
      _ = fresh.num_channels
