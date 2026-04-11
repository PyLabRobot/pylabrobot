import sys
import types
import unittest
from unittest.mock import MagicMock

from pylabrobot.ufactory.xarm6.driver import XArm6Driver, XArm6Error


def _install_mock_xarm(mock_arm: MagicMock) -> MagicMock:
  mock_xarm = types.ModuleType("xarm")
  mock_wrapper = types.ModuleType("xarm.wrapper")
  mock_wrapper.XArmAPI = MagicMock(return_value=mock_arm)  # type: ignore[attr-defined]
  mock_xarm.wrapper = mock_wrapper  # type: ignore[attr-defined]
  sys.modules["xarm"] = mock_xarm
  sys.modules["xarm.wrapper"] = mock_wrapper
  return mock_wrapper.XArmAPI  # type: ignore[attr-defined]


class TestXArm6Driver(unittest.IsolatedAsyncioTestCase):
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

    self.driver = XArm6Driver(ip="192.168.1.113")
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
    self.mock_arm.set_gripper_mode.assert_called_once_with(0)
    self.mock_arm.set_gripper_enable.assert_called_once_with(True)

  async def test_setup_with_tcp_offset(self):
    driver = XArm6Driver(ip="192.168.1.113", tcp_offset=(0, 0, 50, 0, 0, 0))
    await driver.setup()
    self.mock_arm.set_tcp_offset.assert_called_once_with([0, 0, 50, 0, 0, 0])

  async def test_setup_skip_gripper_init(self):
    driver = XArm6Driver(ip="192.168.1.113")
    self.mock_arm.set_gripper_mode.reset_mock()
    self.mock_arm.set_gripper_enable.reset_mock()
    await driver.setup(backend_params=XArm6Driver.SetupParams(skip_gripper_init=True))
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
    result = await self.driver._call_sdk(
      self.mock_arm.emergency_stop, op="emergency_stop"
    )
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
      await self.driver._call_sdk(
        self.mock_arm.move_gohome, op="move_gohome", num_retries=1
      )
    self.assertEqual(ctx.exception.code, 9)
    self.assertEqual(self.mock_arm.move_gohome.call_count, 2)

  async def test_call_sdk_no_retry_by_default(self):
    self.mock_arm.move_gohome.return_value = 9
    with self.assertRaises(XArm6Error):
      await self.driver._call_sdk(self.mock_arm.move_gohome, op="move_gohome")
    self.assertEqual(self.mock_arm.move_gohome.call_count, 1)

  async def test_call_sdk_multi_retry(self):
    self.mock_arm.move_gohome.side_effect = [9, 9, 0]
    await self.driver._call_sdk(
      self.mock_arm.move_gohome, op="move_gohome", num_retries=2
    )
    self.assertEqual(self.mock_arm.move_gohome.call_count, 3)

  async def test_clear_errors_sequence(self):
    self.mock_arm.clean_error.reset_mock()
    self.mock_arm.clean_warn.reset_mock()
    self.mock_arm.motion_enable.reset_mock()
    await self.driver.clear_errors()
    self.mock_arm.clean_error.assert_called_once()
    self.mock_arm.clean_warn.assert_called_once()
    self.mock_arm.motion_enable.assert_called_once_with(True)

