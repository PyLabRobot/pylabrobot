import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.resources import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.ufactory.xarm6.xarm6 import XArm6, XArm6Error


def _install_mock_xarm(mock_arm: MagicMock) -> MagicMock:
  mock_xarm = types.ModuleType("xarm")
  mock_wrapper = types.ModuleType("xarm.wrapper")
  mock_wrapper.XArmAPI = MagicMock(return_value=mock_arm)  # type: ignore[attr-defined]
  mock_xarm.wrapper = mock_wrapper  # type: ignore[attr-defined]
  sys.modules["xarm"] = mock_xarm
  sys.modules["xarm.wrapper"] = mock_wrapper
  return mock_wrapper.XArmAPI  # type: ignore[attr-defined,no-any-return]


class TestXArm6SDK(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_arm = MagicMock()
    self.mock_arm.clean_error.return_value = 0
    self.mock_arm.clean_warn.return_value = 0
    self.mock_arm.motion_enable.return_value = 0
    self.mock_arm.set_mode.return_value = 0
    self.mock_arm.set_state.return_value = 0
    self.mock_arm.set_tcp_offset.return_value = 0
    self.mock_arm.set_tcp_load.return_value = 0
    self.mock_arm.set_gripper_mode.return_value = 0
    self.mock_arm.set_gripper_enable.return_value = 0
    self.mock_arm.disconnect.return_value = None
    self.mock_arm.get_position.return_value = [0, [100, 200, 300, 180, 0, 90]]
    self.mock_arm.set_position.return_value = 0

    self.MockXArmAPI = _install_mock_xarm(self.mock_arm)

    self.driver = XArm6(ip="192.168.1.113")
    await self.driver.setup()

  async def asyncTearDown(self):
    sys.modules.pop("xarm", None)
    sys.modules.pop("xarm.wrapper", None)

  async def test_setup(self):
    self.MockXArmAPI.assert_called_once_with("192.168.1.113")
    self.mock_arm.clean_error.assert_called_once()
    self.mock_arm.clean_warn.assert_called_once()
    self.mock_arm.motion_enable.assert_called_once_with(True)
    self.mock_arm.set_mode.assert_called_with(0)
    self.mock_arm.set_state.assert_called_with(0)
    self.mock_arm.set_gripper_mode.assert_called_once_with(1)
    self.mock_arm.set_gripper_enable.assert_called_once_with(True)

  async def test_setup_with_tcp_offset(self):
    driver = XArm6(ip="192.168.1.113", tcp_offset=(0, 0, 50, 0, 0, 0))
    await driver.setup()
    self.mock_arm.set_tcp_offset.assert_called_once_with([0, 0, 50, 0, 0, 0])

  async def test_setup_skip_gripper_init(self):
    driver = XArm6(ip="192.168.1.113")
    self.mock_arm.set_gripper_mode.reset_mock()
    self.mock_arm.set_gripper_enable.reset_mock()
    await driver.setup(skip_gripper_init=True)
    self.mock_arm.set_gripper_mode.assert_not_called()
    self.mock_arm.set_gripper_enable.assert_not_called()

  async def test_stop(self):
    await self.driver.stop()
    self.mock_arm.disconnect.assert_called_once()
    self.assertIsNone(self.driver._arm)

  async def test_call_sdk_command_success(self):
    result = await self.driver._call_sdk(
      self.mock_arm.set_position, x=1, y=2, z=3, op="set_position"
    )
    self.assertIsNone(result)
    self.mock_arm.set_position.assert_called_with(x=1, y=2, z=3)

  async def test_call_sdk_command_failure_raises(self):
    self.mock_arm.set_position.return_value = -2
    with self.assertRaises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.set_position, op="set_position")
    self.assertEqual(ctx.exception.code, -2)

  async def test_call_sdk_query_unwraps_data(self):
    pose = await self.driver._call_sdk(self.mock_arm.get_position, op="get_position")
    self.assertEqual(pose, [100, 200, 300, 180, 0, 90])

  async def test_call_sdk_query_failure_raises(self):
    self.mock_arm.get_position.return_value = [-1, None]
    with self.assertRaises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.get_position, op="get_position")
    self.assertEqual(ctx.exception.code, -1)

  async def test_call_sdk_ignores_none_return(self):
    self.mock_arm.emergency_stop.return_value = None
    result = await self.driver._call_sdk(self.mock_arm.emergency_stop, op="emergency_stop")
    self.assertIsNone(result)

  async def test_call_sdk_retries_after_clear_errors(self):
    self.mock_arm.move_gohome.side_effect = [9, 0]
    self.mock_arm.clean_error.reset_mock()
    await self.driver._call_sdk(
      self.mock_arm.move_gohome, speed=50, op="move_gohome", num_retries=1
    )
    # Called twice (once failing, once succeeding after clear_errors).
    self.assertEqual(self.mock_arm.move_gohome.call_count, 2)
    self.mock_arm.clean_error.assert_called_once()

  async def test_call_sdk_reraises_if_all_retries_fail(self):
    self.mock_arm.move_gohome.side_effect = [9, 9]
    with self.assertRaises(XArm6Error) as ctx:
      await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome", num_retries=1)
    self.assertEqual(ctx.exception.code, 9)
    self.assertEqual(self.mock_arm.move_gohome.call_count, 2)

  async def test_call_sdk_no_retry_by_default(self):
    self.mock_arm.move_gohome.return_value = 9
    with self.assertRaises(XArm6Error):
      await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome")
    self.assertEqual(self.mock_arm.move_gohome.call_count, 1)

  async def test_call_sdk_multi_retry(self):
    self.mock_arm.move_gohome.side_effect = [9, 9, 0]
    await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome", num_retries=2)
    self.assertEqual(self.mock_arm.move_gohome.call_count, 3)

  async def test_clear_errors_sequence(self):
    self.mock_arm.clean_error.reset_mock()
    self.mock_arm.clean_warn.reset_mock()
    self.mock_arm.motion_enable.reset_mock()
    await self.driver.clear_errors()
    self.mock_arm.clean_error.assert_called_once()
    self.mock_arm.clean_warn.assert_called_once()
    self.mock_arm.motion_enable.assert_called_once_with(True)


class TestXArm6Motion(unittest.IsolatedAsyncioTestCase):
  def _make_device(self, **kwargs) -> XArm6:
    """Build an XArm6 sharing the mock arm and the _call_sdk recorder."""
    device = XArm6(ip="192.168.1.113", **kwargs)
    device._arm = self.arm
    device._call_sdk = self.call_sdk  # type: ignore[method-assign]
    device.clear_errors = AsyncMock()  # type: ignore[method-assign]
    return device

  def setUp(self):
    self.arm = MagicMock()

    sdk_returns = {
      self.arm.get_position: [100, 200, 300, 180, 0, 90],
      self.arm.get_servo_angle: [10, 20, 30, 40, 50, 60],
      self.arm.get_gripper_position: 850,
    }

    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return sdk_returns.get(func)

    self.call_sdk = AsyncMock(side_effect=call_sdk)
    self.device = self._make_device()

  def _sdk_calls_for(self, func) -> list:
    return [c for c in self.call_sdk.call_args_list if c.args and c.args[0] is func]

  # -- Gripper ---------------------------------------------------------------

  async def test_move_gripper_mm_to_units(self):
    # Default range is 71..150 mm mapped to 0..850 units, so 85 mm → ~151 units.
    await self.device.move_gripper(width=85, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].args[1], 151)
    self.assertEqual(calls[0].kwargs["wait"], True)
    self.assertEqual(calls[0].kwargs["speed"], 0)

  async def test_move_gripper_midpoint(self):
    # Midpoint of the 71..150 range is 110.5 mm → 425 units.
    await self.device.move_gripper(width=110.5, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 425)

  async def test_move_gripper_force_sensing(self):
    await self.device.move_gripper(width=71, force_sensing=True)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 0)

  async def test_move_gripper_clamped_high(self):
    await self.device.move_gripper(width=200, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 850)

  async def test_move_gripper_clamped_low(self):
    await self.device.move_gripper(width=-5, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(calls[0].args[1], 0)

  async def test_is_gripper_closed_true(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 5

    self.device._call_sdk = AsyncMock(side_effect=call_sdk)  # type: ignore[method-assign]
    self.assertTrue(await self.device.is_gripper_closed())

  async def test_is_gripper_closed_false(self):
    async def call_sdk(func, *args, op="", num_retries=0, **kwargs):
      return 500

    self.device._call_sdk = AsyncMock(side_effect=call_sdk)  # type: ignore[method-assign]
    self.assertFalse(await self.device.is_gripper_closed())

  # -- Base arm --------------------------------------------------------------

  async def test_halt(self):
    await self.device.halt()
    self.assertEqual(len(self._sdk_calls_for(self.arm.emergency_stop)), 1)

  async def test_park_default_home_uses_retry(self):
    await self.device.park()
    calls = self._sdk_calls_for(self.arm.move_gohome)
    self.assertEqual(len(calls), 1)
    self.assertEqual(calls[0].kwargs["num_retries"], 1)
    self.assertEqual(len(self._sdk_calls_for(self.arm.set_position)), 0)

  async def test_park_with_location(self):
    device = self._make_device(
      park_location=Coordinate(x=250, y=0, z=300),
      park_rotation=Rotation(x=180, y=0, z=0),
    )
    await device.park()
    self.assertEqual(len(self._sdk_calls_for(self.arm.move_gohome)), 0)
    set_pos_calls = self._sdk_calls_for(self.arm.set_position)
    self.assertEqual(len(set_pos_calls), 1)
    self.assertEqual(set_pos_calls[0].kwargs["x"], 250)
    self.assertEqual(set_pos_calls[0].kwargs["y"], 0)
    self.assertEqual(set_pos_calls[0].kwargs["z"], 300)

  async def test_request_gripper_pose(self):
    location = await self.device.request_gripper_pose()
    self.assertEqual(location.location.x, 100)
    self.assertEqual(location.location.y, 200)
    self.assertEqual(location.location.z, 300)
    self.assertEqual(location.rotation.x, 180)
    self.assertEqual(location.rotation.y, 0)
    self.assertEqual(location.rotation.z, 90)

  # -- Cartesian motion ------------------------------------------------------

  async def test_move_to_location_defaults(self):
    await self.device.move_to_location(Coordinate(x=300, y=100, z=200), Rotation(x=180, y=0, z=0))
    calls = self._sdk_calls_for(self.arm.set_position)
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

  async def test_move_to_location_with_motion_profile(self):
    await self.device.move_to_location(
      Coordinate(x=0, y=0, z=0),
      Rotation(),
      speed=250,
      mvacc=3500,
    )
    calls = self._sdk_calls_for(self.arm.set_position)
    self.assertEqual(calls[0].kwargs["speed"], 250)
    self.assertEqual(calls[0].kwargs["mvacc"], 3500)

  async def test_pick_up_at_location_move_then_close(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.device.pick_up_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.arm.set_position)
    self.assertEqual(len(mcalls), 1)
    self.assertEqual(mcalls[0].kwargs["z"], 50)

    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    # 80 mm in 71..150 mm range → (80-71)/(150-71) * 850 ≈ 97 units.
    self.assertEqual(grip_calls[0].args[1], 97)

  async def test_drop_at_location_move_then_open_max(self):
    loc = Coordinate(x=300, y=100, z=50)
    rot = Rotation(x=180, y=0, z=0)
    await self.device.drop_at_location(loc, rot, resource_width=80)

    mcalls = self._sdk_calls_for(self.arm.set_position)
    self.assertEqual(len(mcalls), 1)
    self.assertEqual(mcalls[0].kwargs["z"], 50)

    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 850)  # SDK max

  # -- Joints ----------------------------------------------------------------

  async def test_request_joint_position(self):
    result = await self.device.request_joint_position()
    self.assertEqual(result, {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60})

  async def test_move_to_joint_position_partial(self):
    await self.device.move_to_joint_position({1: 45, 3: -90})
    self.assertEqual(len(self._sdk_calls_for(self.arm.get_servo_angle)), 1)
    set_calls = self._sdk_calls_for(self.arm.set_servo_angle)
    self.assertEqual(len(set_calls), 1)
    self.assertEqual(set_calls[0].kwargs["angle"], [45, 20, -90, 40, 50, 60])
    self.assertEqual(set_calls[0].kwargs["speed"], 50.0)
    self.assertEqual(set_calls[0].kwargs["mvacc"], 500.0)
    self.assertEqual(set_calls[0].kwargs["wait"], True)

  async def test_move_to_joint_position_with_motion_profile(self):
    await self.device.move_to_joint_position(
      {1: 0},
      speed=120,
      mvacc=900,
    )
    set_calls = self._sdk_calls_for(self.arm.set_servo_angle)
    self.assertEqual(set_calls[0].kwargs["speed"], 120)
    self.assertEqual(set_calls[0].kwargs["mvacc"], 900)

  async def test_pick_up_at_joint_position(self):
    await self.device.pick_up_at_joint_position({1: 0, 2: 0}, resource_width=80)
    self.assertEqual(len(self._sdk_calls_for(self.arm.set_servo_angle)), 1)
    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 97)

  async def test_drop_at_joint_position(self):
    await self.device.drop_at_joint_position({1: 0, 2: 0}, resource_width=80)
    self.assertEqual(len(self._sdk_calls_for(self.arm.set_servo_angle)), 1)
    grip_calls = self._sdk_calls_for(self.arm.set_gripper_position)
    self.assertEqual(len(grip_calls), 1)
    self.assertEqual(grip_calls[0].args[1], 850)

  # -- Freedrive -------------------------------------------------------------

  async def test_start_freedrive_mode(self):
    await self.device.start_freedrive_mode(free_axes=[0])
    mode_calls = self._sdk_calls_for(self.arm.set_mode)
    state_calls = self._sdk_calls_for(self.arm.set_state)
    self.assertEqual(mode_calls[0].args[1], 2)
    self.assertEqual(state_calls[0].args[1], 0)

  async def test_stop_freedrive_mode(self):
    await self.device.stop_freedrive_mode()
    mode_calls = self._sdk_calls_for(self.arm.set_mode)
    state_calls = self._sdk_calls_for(self.arm.set_state)
    self.assertEqual(mode_calls[0].args[1], 0)
    self.assertEqual(state_calls[0].args[1], 0)

  # -- Custom configuration --------------------------------------------------

  async def test_custom_gripper_range(self):
    device = self._make_device(gripper_min_mm=50.0, gripper_max_mm=100.0)
    await device.move_gripper(width=75.0, force_sensing=False)
    calls = self._sdk_calls_for(self.arm.set_gripper_position)
    # Midpoint of 50..100 mm range → 0.5 * 850 = 425 units.
    self.assertEqual(calls[0].args[1], 425)
