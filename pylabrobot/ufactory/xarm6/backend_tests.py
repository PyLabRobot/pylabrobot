import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.ufactory.xarm6.backend import XArm6ArmBackend
from pylabrobot.ufactory.xarm6.driver import XArm6Driver


class TestXArm6ArmBackend(unittest.IsolatedAsyncioTestCase):
  def _make_driver(self) -> MagicMock:
    """Build a mock driver whose _call_sdk returns sensible values per SDK method."""
    driver = MagicMock(spec=XArm6Driver)
    driver._arm = MagicMock()

    sdk_returns = {
      driver._arm.get_position: [100, 200, 300, 180, 0, 90],
      driver._arm.get_servo_angle: [10, 20, 30, 40, 50, 60],
      driver._arm.get_gripper_position: 850,
    }

    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return sdk_returns.get(func)

    driver._call_sdk = AsyncMock(side_effect=call_sdk)
    driver.clear_errors = AsyncMock()
    return driver

  def setUp(self):
    self.driver = self._make_driver()
    self.backend = XArm6ArmBackend(driver=self.driver)

  def _sdk_calls_for(self, func) -> list:
    return [c for c in self.driver._call_sdk.call_args_list if c.args and c.args[0] is func]

  # -- Gripper ---------------------------------------------------------------

  async def test_open_gripper_mm_to_units(self):
    await self.backend.open_gripper(gripper_width=85)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].args[1], 850)
    self.assertEqual(calls[0].kwargs["wait"], True)
    self.assertEqual(calls[0].kwargs["speed"], 0)

  async def test_open_gripper_half(self):
    await self.backend.open_gripper(gripper_width=42.5)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 425)

  async def test_close_gripper(self):
    await self.backend.close_gripper(gripper_width=0)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 0)

  async def test_open_gripper_clamped_high(self):
    await self.backend.open_gripper(gripper_width=200)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 850)

  async def test_open_gripper_clamped_low(self):
    await self.backend.open_gripper(gripper_width=-5)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 0)

  async def test_is_gripper_closed_true(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 5

    self.driver._call_sdk = AsyncMock(side_effect=call_sdk)
    self.assertTrue(await self.backend.is_gripper_closed())

  async def test_is_gripper_closed_false(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 500

    self.driver._call_sdk = AsyncMock(side_effect=call_sdk)
    self.assertFalse(await self.backend.is_gripper_closed())

  # -- Base arm --------------------------------------------------------------

  async def test_halt(self):
    await self.backend.halt()
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.emergency_stop)), 1)

  async def test_park_default_home_uses_retry(self):
    await self.backend.park()
    calls = self._sdk_calls_for(self.driver._arm.move_gohome)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].kwargs["num_retries"], 1)
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.set_position)), 0)

  async def test_park_with_location(self):
    backend = XArm6ArmBackend(
      driver=self.driver,
      park_location=Coordinate(x=250, y=0, z=300),
      park_rotation=Rotation(x=180, y=0, z=0),
    )
    await backend.park()
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.move_gohome)), 0)
    set_pos_calls = self._sdk_calls_for(self.driver._arm.set_position)
    self.assertEqual(len(set_pos_calls), 1)
    self.assertEqual(set_pos_calls[0].kwargs["x"], 250)
    self.assertEqual(set_pos_calls[0].kwargs["y"], 0)
    self.assertEqual(set_pos_calls[0].kwargs["z"], 300)

  async def test_request_gripper_location(self):
    location = await self.backend.request_gripper_location()
    self.assertEqual(location.location.x, 100)
    self.assertEqual(location.location.y, 200)
    self.assertEqual(location.location.z, 300)
    self.assertEqual(location.rotation.x, 180)
    self.assertEqual(location.rotation.y, 0)
    self.assertEqual(location.rotation.z, 90)

  # -- Cartesian motion ------------------------------------------------------

  async def test_move_to_location_defaults(self):
    await self.backend.move_to_location(Coordinate(x=300, y=100, z=200), Rotation(x=180, y=0, z=0))
    calls = self._sdk_calls_for(self.driver._arm.set_position)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].kwargs["x"], 300)
    self.assertEqual(calls[0].kwargs["y"], 100)
    self.assertEqual(calls[0].kwargs["z"], 200)
    self.assertEqual(calls[0].kwargs["roll"], 180)
    self.assertEqual(calls[0].kwargs["pitch"], 0)
    self.assertEqual(calls[0].kwargs["yaw"], 0)
    self.assertEqual(calls[0].kwargs["speed"], 100.0)
    self.assertEqual(calls[0].kwargs["mvacc"], 2000.0)
    self.assertEqual(calls[0].kwargs["wait"], True)

  async def test_move_to_location_with_backend_params(self):
    await self.backend.move_to_location(
      Coordinate(x=0, y=0, z=0),
      Rotation(),
      backend_params=XArm6ArmBackend.CartesianMoveParams(speed=250, mvacc=3500),
    )
    calls = self._sdk_calls_for(self.driver._arm.set_position)
    self.assertEqual(calls[0].kwargs["speed"], 250)
    self.assertEqual(calls[0].kwargs["mvacc"], 3500)

  async def test_pick_up_at_location_move_then_close(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.backend.pick_up_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.driver._arm.set_position)
    self.assertEqual(len(mcalls), 1)
    self.assertEqual(mcalls[0].kwargs["z"], 50)

    grip_calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 800)  # 80 mm → 800 units

  async def test_drop_at_location_move_then_open_max(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.backend.drop_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.driver._arm.set_position)
    self.assertEqual(len(mcalls), 1)
    self.assertEqual(mcalls[0].kwargs["z"], 50)

    grip_calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 850)  # SDK max

  # -- Joints ----------------------------------------------------------------

  async def test_request_joint_position(self):
    result = await self.backend.request_joint_position()
    self.assertEqual(result, {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60})

  async def test_move_to_joint_position_partial(self):
    await self.backend.move_to_joint_position({1: 45, 3: -90})
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.get_servo_angle)), 1)
    set_calls = self._sdk_calls_for(self.driver._arm.set_servo_angle)
    self.assertEqual(len(set_calls), 1)
    self.assertEqual(set_calls[0].kwargs["angle"], [45, 20, -90, 40, 50, 60])
    self.assertEqual(set_calls[0].kwargs["speed"], 50.0)
    self.assertEqual(set_calls[0].kwargs["mvacc"], 500.0)
    self.assertEqual(set_calls[0].kwargs["wait"], True)

  async def test_move_to_joint_position_with_backend_params(self):
    await self.backend.move_to_joint_position(
      {1: 0},
      backend_params=XArm6ArmBackend.JointMoveParams(speed=120, mvacc=900),
    )
    set_calls = self._sdk_calls_for(self.driver._arm.set_servo_angle)
    self.assertEqual(set_calls[0].kwargs["speed"], 120)
    self.assertEqual(set_calls[0].kwargs["mvacc"], 900)

  async def test_pick_up_at_joint_position(self):
    await self.backend.pick_up_at_joint_position({1: 0, 2: 0}, resource_width=80)
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.set_servo_angle)), 1)
    grip_calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 800)

  async def test_drop_at_joint_position(self):
    await self.backend.drop_at_joint_position({1: 0, 2: 0}, resource_width=80)
    self.assertEqual(len(self._sdk_calls_for(self.driver._arm.set_servo_angle)), 1)
    grip_calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 850)

  # -- Freedrive -------------------------------------------------------------

  async def test_start_freedrive_mode(self):
    await self.backend.start_freedrive_mode(free_axes=[0])
    mode_calls = self._sdk_calls_for(self.driver._arm.set_mode)
    state_calls = self._sdk_calls_for(self.driver._arm.set_state)
    self.assertEqual(mode_calls[0].args[1], 2)
    self.assertEqual(state_calls[0].args[1], 0)

  async def test_stop_freedrive_mode(self):
    await self.backend.stop_freedrive_mode()
    mode_calls = self._sdk_calls_for(self.driver._arm.set_mode)
    state_calls = self._sdk_calls_for(self.driver._arm.set_state)
    self.assertEqual(mode_calls[0].args[1], 0)
    self.assertEqual(state_calls[0].args[1], 0)

  # -- Custom configuration --------------------------------------------------

  async def test_custom_mm_per_gripper_unit(self):
    backend = XArm6ArmBackend(driver=self.driver, mm_per_gripper_unit=0.2)
    await backend.open_gripper(gripper_width=85)
    calls = self._sdk_calls_for(self.driver._arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 425)
