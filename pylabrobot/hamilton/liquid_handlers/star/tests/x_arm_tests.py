import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.x_arm import STARXArm


class TestSTARXArmCommands(unittest.IsolatedAsyncioTestCase):
  """Test that STARXArm methods produce the exact same firmware commands as the legacy
  STARBackend equivalents, for both left and right arms."""

  async def asyncSetUp(self):
    self.mock_driver = MagicMock()
    self.mock_driver.send_command = AsyncMock()
    self.left_arm = STARXArm(driver=self.mock_driver, side="left")
    self.right_arm = STARXArm(driver=self.mock_driver, side="right")

  # -- move_to (C0:JX / C0:JS) ----------------------------------------------

  async def test_left_move_to(self):
    await self.left_arm.move_to(x_position=500.0)  # 500 mm -> 05000 in 0.1mm
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="JX",
      xs="05000",
    )

  async def test_right_move_to(self):
    await self.right_arm.move_to(x_position=500.0)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="JS",
      xs="05000",
    )

  async def test_left_move_to_default(self):
    await self.left_arm.move_to()
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="JX",
      xs="00000",
    )

  async def test_right_move_to_default(self):
    await self.right_arm.move_to()
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="JS",
      xs="00000",
    )

  # -- move_to_safe (C0:KX / C0:KR) -----------------------------------------

  async def test_left_move_to_safe(self):
    await self.left_arm.move_to_safe(x_position=1000.0)  # 1000 mm -> 10000 in 0.1mm
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="KX",
      xs=10000,
    )

  async def test_right_move_to_safe(self):
    await self.right_arm.move_to_safe(x_position=1000.0)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="KR",
      xs=10000,
    )

  async def test_left_move_to_safe_default(self):
    await self.left_arm.move_to_safe()
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="KX",
      xs=0,
    )

  async def test_right_move_to_safe_default(self):
    await self.right_arm.move_to_safe()
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="KR",
      xs=0,
    )

  # -- request_position (C0:RX / C0:QX) -------------------------------------

  async def test_left_request_position(self):
    self.mock_driver.send_command.return_value = {"rx": 15000}
    result = await self.left_arm.request_position()
    self.assertEqual(result, 1500.0)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="RX",
      fmt="rx#####",
    )

  async def test_right_request_position(self):
    self.mock_driver.send_command.return_value = {"rx": 15000}
    result = await self.right_arm.request_position()
    self.assertEqual(result, 1500.0)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="QX",
      fmt="rx#####",
    )

  # -- last_collision_type (C0:XX / C0:XR) -----------------------------------

  async def test_left_last_collision_type_true(self):
    self.mock_driver.send_command.return_value = {"xq": 1}
    result = await self.left_arm.last_collision_type()
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="XX",
      fmt="xq#",
    )

  async def test_left_last_collision_type_false(self):
    self.mock_driver.send_command.return_value = {"xq": 0}
    result = await self.left_arm.last_collision_type()
    self.assertFalse(result)

  async def test_right_last_collision_type_true(self):
    self.mock_driver.send_command.return_value = {"xq": 1}
    result = await self.right_arm.last_collision_type()
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="XR",
      fmt="xq#",
    )

  async def test_right_last_collision_type_false(self):
    self.mock_driver.send_command.return_value = {"xq": 0}
    result = await self.right_arm.last_collision_type()
    self.assertFalse(result)

  # -- assertion checks ------------------------------------------------------

  async def test_move_to_rejects_out_of_range(self):
    with self.assertRaises(AssertionError):
      await self.left_arm.move_to(x_position=-1)
    with self.assertRaises(AssertionError):
      await self.left_arm.move_to(x_position=3001)
    with self.assertRaises(AssertionError):
      await self.right_arm.move_to(x_position=-1)

  async def test_move_to_safe_rejects_out_of_range(self):
    with self.assertRaises(AssertionError):
      await self.left_arm.move_to_safe(x_position=-1)
    with self.assertRaises(AssertionError):
      await self.right_arm.move_to_safe(x_position=3001)
