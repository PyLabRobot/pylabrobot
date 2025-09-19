import unittest

from pylabrobot.arms.backend import CartesianCoords, ElbowOrientation, JointCoords
from pylabrobot.arms.precise_flex.precise_flex_backend import PreciseFlexBackend


class PreciseFlexBackendHardwareTests(unittest.IsolatedAsyncioTestCase):
  """Integration tests for PreciseFlex robot - RUNS ON ACTUAL HARDWARE"""
    # Connection config
  MODEL = "pf3400"
  ROBOT_HOST = "192.168.0.1"
  ROBOT_PORT = 10100

  # Test constants
  TEST_PROFILE_ID = 20
  TEST_LOCATION_ID = 20
  TEST_PARAMETER_ID = 17018
  TEST_SIGNAL_ID = 20064

  SAFE_LOCATION_C = CartesianCoords(175, 0, 169.994, -0.001, 90, 180, ElbowOrientation.RIGHT)
  SAFE_LOCATION_J = JointCoords(0, 170.003, 0, 180, -180, 75.486)

  TEST_LOCATION_J_LEFT = JointCoords(0, 169.932, 16.883, 230.942, -224.288, 75.662)
  TEST_LOCATION_C_LEFT = CartesianCoords(328.426, -115.219, 169.932, 23.537, 90, 180, ElbowOrientation.LEFT)

  TEST_LOCATION_J_RIGHT = JointCoords(0, 169.968, -4.238, 117.915, -100.062, 75.668)
  TEST_LOCATION_C_RIGHT = CartesianCoords(342.562, 280.484, 169.969, 13.612, 90, 180, ElbowOrientation.RIGHT)

  async def asyncSetUp(self):
    """Connect to actual PreciseFlex robot"""
    self.robot = PreciseFlexBackend(self.MODEL, self.ROBOT_HOST, self.ROBOT_PORT)
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

  async def test_approach_c(self):
    await self.robot.approach(self.TEST_LOCATION_C_LEFT, 20)

  async def test_approach_j(self):
    await self.robot.approach(self.TEST_LOCATION_J_LEFT, 20)

  async def test_pick_plate(self):
    try:
      await self.robot.pick_plate(self.TEST_LOCATION_C_RIGHT, 10)
    except Exception as e:
      if 'no plate present' in str(e).lower():
        pass
      else:
        raise

  async def test_place_plate(self):
    await self.robot.place_plate(self.TEST_LOCATION_C_LEFT, 15)

  async def test_move_to_j(self):
    await self.robot.move_to(self.TEST_LOCATION_J_RIGHT)

  async def test_move_to_c(self):
    await self.robot.move_to(self.TEST_LOCATION_C_RIGHT)

  async def test_get_position_j(self):
    """Test getting joint position"""
    position_j = await self.robot.get_joint_position()
    self.assertIsInstance(position_j, JointCoords)

  async def test_get_position_c(self):
    """Test getting cartesian position"""
    position_c = await self.robot.get_cartesian_position()
    self.assertIsInstance(position_c, CartesianCoords)


