import unittest

from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend
import asyncio


class PreciseFlexBackendHardwareTests(unittest.IsolatedAsyncioTestCase):
  """Integration tests for PreciseFlex robot - RUNS ON ACTUAL HARDWARE"""

  async def asyncSetUp(self):
    """Connect to actual PreciseFlex robot"""
    self.robot = PreciseFlexBackend("192.168.0.1", 10100)
    await self.robot.setup()

  async def asyncTearDown(self):
    """Cleanup robot connection"""
    if hasattr(self, 'robot'):
      await self.robot.stop()

  async def test_set_speed(self):
    await self.robot.set_speed(50)
    speed = await self.robot.get_speed()
    self.assertEqual(speed, 50)

  async def test_open_close_gripper(self):
    await self.robot.close_gripper()
    closed = await self.robot.is_gripper_closed()
    self.assertTrue(closed)

    await self.robot.open_gripper()
    closed = await self.robot.is_gripper_closed()
    self.assertFalse(closed)

  async def test_home(self):
    await self.robot.home()

  async def test_move_to_safe(self):
    await self.robot.move_to_safe()

  async def test_approach_j(self):
    current_c = await self.robot.get_position_c()
    # create a position that is very close to current position for each dimension
    target_c = (
        current_c[0] + 0.01, current_c[1] + 0.01, current_c[2] + 0.01,
        current_c[3] + 0.01, current_c[4] + 0.01, current_c[5] + 0.01
    )
    await self.robot.approach_c(target_c, 10, current_c[-1])

  async def test_pick_plate_j(self):
    current_c = await self.robot.get_position_c()
    # create a position that is very close to current position for each dimension
    target_c = (
        current_c[0] + 0.01, current_c[1] + 0.01, current_c[2] + 0.01,
        current_c[3] + 0.01, current_c[4] + 0.01, current_c[5] + 0.01
    )
    await self.robot.pick_plate_c(target_c, 10, current_c[-1])

  async def test_place_plate_j(self):
    current_c = await self.robot.get_position_c()
    # create a position that is very close to current position for each dimension
    target_c = (
        current_c[0] + 0.01, current_c[1] + 0.01, current_c[2] + 0.01,
        current_c[3] + 0.01, current_c[4] + 0.01, current_c[5] + 0.01
    )
    await self.robot.place_plate_c(target_c, 10, current_c[-1])

  async def test_move_to_j(self):
    current_c = await self.robot.get_position_c()
    # create a position that is very close to current position for each dimension
    target_c = (
        current_c[0] + 0.01, current_c[1] + 0.01, current_c[2] + 0.01,
        current_c[3] + 0.01, current_c[4] + 0.01, current_c[5] + 0.01
    )
    await self.robot.move_to_c(target_c, current_c[-1])

  async def test_get_position_j(self):
    """Test getting joint position"""
    position_j = await self.robot.get_position_j()
    self.assertIsInstance(position_j, tuple)
    self.assertEqual(len(position_j), 6)

  async def test_get_position_c(self):
    """Test getting cartesian position"""
    position_c = await self.robot.get_position_c()
    self.assertIsInstance(position_c, tuple)
    self.assertEqual(len(position_c), 6)

  async def test_move_to_j_joints(self):
    """Test joint movement"""
    current_j = await self.robot.get_position_j()
    # Small joint movements
    target_j = (
      current_j[0] + 0.1, current_j[1] + 0.1, current_j[2] + 0.1,
      current_j[3] + 0.1, current_j[4] + 0.1, current_j[5] + 0.1, current_j[6] + 0.1
    )
    await self.robot.move_to_j(target_j)

  async def test_approach_j_joints(self):
    """Test joint approach movement"""
    current_j = await self.robot.get_position_j()
    target_j = (
      current_j[0] + 0.1, current_j[1] + 0.1, current_j[2] + 0.1,
      current_j[3] + 0.1, current_j[4] + 0.1, current_j[5] + 0.1, current_j[6] + 0.1
    )
    await self.robot.approach_j(target_j, 10)

  async def test_pick_plate_j_joints(self):
    """Test joint pick plate movement"""
    current_j = await self.robot.get_position_j()
    target_j = (
      current_j[0] + 0.1, current_j[1] + 0.1, current_j[2] + 0.1,
      current_j[3] + 0.1, current_j[4] + 0.1, current_j[5] + 0.1, current_j[6] + 0.1
    )
    await self.robot.pick_plate_j(target_j, 10)

  async def test_place_plate_j_joints(self):
    """Test joint place plate movement"""
    current_j = await self.robot.get_position_j()
    target_j = (
      current_j[0] + 0.1, current_j[1] + 0.1, current_j[2] + 0.1,
      current_j[3] + 0.1, current_j[4] + 0.1, current_j[5] + 0.1, current_j[6] + 0.1
    )
    await self.robot.place_plate_j(target_j, 10)
