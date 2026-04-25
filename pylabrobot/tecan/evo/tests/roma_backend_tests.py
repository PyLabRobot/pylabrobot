"""Unit tests for EVORoMaBackend."""

import unittest
from unittest.mock import AsyncMock

from pylabrobot.resources import EVO150Deck
from pylabrobot.resources.tecan.plate_carriers import MP_3Pos
from pylabrobot.resources.tecan.plates import Microplate_96_Well
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.firmware import RoMa
from pylabrobot.tecan.evo.roma_backend import EVORoMaBackend


class RoMaTestBase(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    super().setUp()

    self.driver = TecanEVODriver()
    self.driver.send_command = AsyncMock()

    async def mock_send(module, command, params=None, **kwargs):
      if command == "RPX":
        return {"data": [9000]}
      if command == "RPY":
        return {"data": [2000]}
      if command == "RPZ":
        return {"data": [2500]}
      if command == "RPR":
        return {"data": [1800]}
      if command == "RPG":
        return {"data": [900]}
      return {"data": []}

    self.driver.send_command.side_effect = mock_send

    self.deck = EVO150Deck()
    self.backend = EVORoMaBackend(driver=self.driver, deck=self.deck)
    self.backend.roma = RoMa(self.driver, "C1")

    self.plate_carrier = MP_3Pos(name="carrier")
    self.plate_carrier[0] = self.plate = Microplate_96_Well(name="plate")
    self.deck.assign_child_resource(self.plate_carrier, rails=20)

    self.driver.send_command.reset_mock()


class RoMaParkTests(RoMaTestBase):
  async def test_park_sends_saa_aac(self):
    await self.backend.park()
    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    self.assertIn("SAA", cmd_names)
    self.assertIn("AAC", cmd_names)


class RoMaHaltTests(RoMaTestBase):
  async def test_halt_sends_bma(self):
    await self.backend.halt()
    self.driver.send_command.assert_called_with(module="C1", command="BMA", params=[0, 0, 0])


class RoMaGripperTests(RoMaTestBase):
  async def test_open_gripper(self):
    await self.backend.open_gripper(gripper_width=90.0)
    self.driver.send_command.assert_called_with(module="C1", command="PAG", params=[900])

  async def test_close_gripper(self):
    await self.backend.close_gripper(gripper_width=50.0)
    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    self.assertIn("SGG", cmd_names)
    self.assertIn("AGR", cmd_names)

  async def test_is_gripper_closed(self):
    result = await self.backend.is_gripper_closed()
    # Mock returns RPG=900, which is >= 100, so not closed
    self.assertFalse(result)


class RoMaLocationTests(RoMaTestBase):
  async def test_get_gripper_location(self):
    loc = await self.backend.get_gripper_location()
    self.assertEqual(loc.location.x, 900.0)  # 9000 / 10
    self.assertEqual(loc.location.y, 200.0)  # 2000 / 10
    self.assertEqual(loc.location.z, 250.0)  # 2500 / 10
    self.assertEqual(loc.rotation.z, 180.0)  # 1800 / 10


class RoMaPickUpTests(RoMaTestBase):
  async def test_pick_up_from_carrier_sends_trajectory(self):
    await self.backend.pick_up_from_carrier(self.plate)
    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    # Should send speed configs, SAA (vector coords), AAC (execute), SGG (gripper), AGR (grip)
    self.assertIn("SFX", cmd_names)
    self.assertIn("SAA", cmd_names)
    self.assertIn("AAC", cmd_names)
    self.assertIn("SGG", cmd_names)
    self.assertIn("AGR", cmd_names)


class RoMaDropTests(RoMaTestBase):
  async def test_drop_at_carrier_sends_trajectory(self):
    dest = self.plate.get_location_wrt(self.deck)
    await self.backend.drop_at_carrier(self.plate, dest)
    cmd_names = [c.kwargs.get("command", "?") for c in self.driver.send_command.call_args_list]
    # Multi-point trajectory: STW (target windows), SAA (vector coords), AAC (execute), PAG (open gripper)
    self.assertIn("STW", cmd_names)
    self.assertIn("SAA", cmd_names)
    self.assertIn("AAC", cmd_names)
    self.assertIn("PAG", cmd_names)
