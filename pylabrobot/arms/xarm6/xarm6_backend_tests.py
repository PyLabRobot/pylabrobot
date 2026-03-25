import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call

from pylabrobot.arms.backend import VerticalAccess, HorizontalAccess
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.arms.xarm6.xarm6_backend import XArm6Backend, XArm6Error
from pylabrobot.resources import Coordinate, Rotation


class TestXArm6Backend(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_arm = MagicMock()
    self.mock_arm.clean_error.return_value = 0
    self.mock_arm.clean_warn.return_value = 0
    self.mock_arm.motion_enable.return_value = 0
    self.mock_arm.set_mode.return_value = 0
    self.mock_arm.set_state.return_value = 0
    self.mock_arm.set_position.return_value = 0
    self.mock_arm.set_servo_angle.return_value = 0
    self.mock_arm.move_gohome.return_value = 0
    self.mock_arm.get_position.return_value = [0, [100, 200, 300, 180, 0, 90]]
    self.mock_arm.get_servo_angle.return_value = [0, [10, 20, 30, 40, 50, 60]]
    self.mock_arm.set_gripper_position.return_value = 0
    self.mock_arm.set_gripper_mode.return_value = 0
    self.mock_arm.set_gripper_enable.return_value = 0
    self.mock_arm.emergency_stop.return_value = None
    self.mock_arm.disconnect.return_value = None

    # Create fake xarm.wrapper module with a mock XArmAPI class
    mock_xarm = types.ModuleType("xarm")
    mock_wrapper = types.ModuleType("xarm.wrapper")
    mock_wrapper.XArmAPI = MagicMock(return_value=self.mock_arm)
    mock_xarm.wrapper = mock_wrapper
    sys.modules["xarm"] = mock_xarm
    sys.modules["xarm.wrapper"] = mock_wrapper
    self.MockXArmAPI = mock_wrapper.XArmAPI

    self.backend = XArm6Backend(ip="192.168.1.113")
    await self.backend.setup()

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

  async def test_stop(self):
    await self.backend.stop()
    self.mock_arm.disconnect.assert_called_once()
    self.assertIsNone(self.backend._arm)

  async def test_move_to_cartesian(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=200),
      rotation=Rotation(x=180, y=0, z=0),
    )
    await self.backend.move_to(pos)
    self.mock_arm.set_position.assert_called_once_with(
      x=300, y=100, z=200,
      roll=180, pitch=0, yaw=0,
      speed=100.0, mvacc=2000.0, wait=True,
    )

  async def test_move_to_joints(self):
    await self.backend.move_to({1: 45, 3: -90})
    self.mock_arm.get_servo_angle.assert_called()
    self.mock_arm.set_servo_angle.assert_called_once_with(
      angle=[45, 20, -90, 40, 50, 60],
      speed=50.0, mvacc=500.0, wait=True,
    )

  async def test_move_to_invalid_type(self):
    with self.assertRaises(TypeError):
      await self.backend.move_to("invalid")

  async def test_home(self):
    await self.backend.home()
    self.mock_arm.move_gohome.assert_called_once_with(speed=50, mvacc=5000, wait=True)

  async def test_halt(self):
    await self.backend.halt()
    self.mock_arm.emergency_stop.assert_called_once()

  async def test_move_to_safe_with_position(self):
    safe_pos = CartesianCoords(
      location=Coordinate(x=250, y=0, z=300),
      rotation=Rotation(x=180, y=0, z=0),
    )
    self.backend._safe_position = safe_pos
    await self.backend.move_to_safe()
    self.mock_arm.set_position.assert_called_once()

  async def test_move_to_safe_no_position(self):
    await self.backend.move_to_safe()
    self.mock_arm.move_gohome.assert_called_once()

  async def test_get_joint_position(self):
    result = await self.backend.get_joint_position()
    self.assertEqual(result, {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60})

  async def test_get_cartesian_position(self):
    result = await self.backend.get_cartesian_position()
    self.assertEqual(result.location.x, 100)
    self.assertEqual(result.location.y, 200)
    self.assertEqual(result.location.z, 300)
    self.assertEqual(result.rotation.x, 180)
    self.assertEqual(result.rotation.y, 0)
    self.assertEqual(result.rotation.z, 90)

  async def test_open_gripper(self):
    await self.backend.open_gripper(position=850)
    self.mock_arm.set_gripper_position.assert_called_once_with(850, wait=True, speed=0)

  async def test_open_gripper_with_speed(self):
    await self.backend.open_gripper(position=850, speed=5)
    self.mock_arm.set_gripper_position.assert_called_once_with(850, wait=True, speed=5)

  async def test_close_gripper(self):
    await self.backend.close_gripper(position=100)
    self.mock_arm.set_gripper_position.assert_called_once_with(100, wait=True, speed=0)

  async def test_freedrive_mode(self):
    await self.backend.freedrive_mode()
    self.mock_arm.set_mode.assert_called_with(2)
    self.mock_arm.set_state.assert_called_with(0)

  async def test_end_freedrive_mode(self):
    await self.backend.end_freedrive_mode()
    self.mock_arm.set_mode.assert_called_with(0)
    self.mock_arm.set_state.assert_called_with(0)

  async def test_error_handling(self):
    self.mock_arm.set_position.return_value = -2
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=200),
      rotation=Rotation(x=180, y=0, z=0),
    )
    with self.assertRaises(XArm6Error) as ctx:
      await self.backend.move_to(pos)
    self.assertEqual(ctx.exception.code, -2)

  # -- Pick/Place sequence tests --

  async def test_pick_up_resource_vertical(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=50),
      rotation=Rotation(x=180, y=0, z=0),
    )
    access = VerticalAccess(approach_height_mm=80, clearance_mm=80, gripper_offset_mm=10)
    await self.backend.pick_up_resource(pos, access=access)

    calls = self.mock_arm.set_position.call_args_list
    # open_gripper, move above (z=130), descend (z=50), close_gripper, retract (z=130)
    gripper_calls = self.mock_arm.set_gripper_position.call_args_list
    self.assertEqual(gripper_calls[0], call(850, wait=True, speed=0))
    self.assertEqual(gripper_calls[1], call(0, wait=True, speed=0))
    self.assertEqual(len(calls), 3)
    # approach: z = 50 + 80 = 130
    self.assertEqual(calls[0].kwargs["z"], 130)
    # target: z = 50
    self.assertEqual(calls[1].kwargs["z"], 50)
    # retract: z = 50 + 80 = 130
    self.assertEqual(calls[2].kwargs["z"], 130)

  async def test_drop_resource_vertical(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=50),
      rotation=Rotation(x=180, y=0, z=0),
    )
    access = VerticalAccess(approach_height_mm=80, clearance_mm=80, gripper_offset_mm=10)
    await self.backend.drop_resource(pos, access=access)

    calls = self.mock_arm.set_position.call_args_list
    # approach (z=50+10+80=140), place (z=50+10=60), open_gripper, retract (z=140)
    self.assertEqual(len(calls), 3)
    self.assertEqual(calls[0].kwargs["z"], 140)
    self.assertEqual(calls[1].kwargs["z"], 60)
    self.assertEqual(calls[2].kwargs["z"], 140)
    self.mock_arm.set_gripper_position.assert_called_once_with(850, wait=True, speed=0)

  async def test_pick_up_resource_horizontal(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=50),
      rotation=Rotation(x=180, y=0, z=0),
    )
    access = HorizontalAccess(
      approach_distance_mm=50, clearance_mm=50, lift_height_mm=100, gripper_offset_mm=10
    )
    await self.backend.pick_up_resource(pos, access=access)

    calls = self.mock_arm.set_position.call_args_list
    # open, approach (y=50), target (y=100), close, retract (y=50), lift (y=50, z=150)
    gripper_calls = self.mock_arm.set_gripper_position.call_args_list
    self.assertEqual(gripper_calls[0], call(850, wait=True, speed=0))
    self.assertEqual(gripper_calls[1], call(0, wait=True, speed=0))
    self.assertEqual(len(calls), 4)
    # approach: y = 100 - 50 = 50
    self.assertEqual(calls[0].kwargs["y"], 50)
    # target: y = 100
    self.assertEqual(calls[1].kwargs["y"], 100)
    # retract: y = 100 - 50 = 50
    self.assertEqual(calls[2].kwargs["y"], 50)
    # lift: y = 50, z = 50 + 100 = 150
    self.assertEqual(calls[3].kwargs["y"], 50)
    self.assertEqual(calls[3].kwargs["z"], 150)

  async def test_approach_vertical(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=50),
      rotation=Rotation(x=180, y=0, z=0),
    )
    access = VerticalAccess(approach_height_mm=80)
    await self.backend.approach(pos, access=access)

    self.mock_arm.set_position.assert_called_once()
    call_kwargs = self.mock_arm.set_position.call_args.kwargs
    self.assertEqual(call_kwargs["z"], 130)

  async def test_approach_horizontal(self):
    pos = CartesianCoords(
      location=Coordinate(x=300, y=100, z=50),
      rotation=Rotation(x=180, y=0, z=0),
    )
    access = HorizontalAccess(approach_distance_mm=50)
    await self.backend.approach(pos, access=access)

    self.mock_arm.set_position.assert_called_once()
    call_kwargs = self.mock_arm.set_position.call_args.kwargs
    self.assertEqual(call_kwargs["y"], 50)

  async def test_pick_requires_cartesian(self):
    with self.assertRaises(TypeError):
      await self.backend.pick_up_resource({1: 45})

  async def test_setup_with_tcp_offset(self):
    backend = XArm6Backend(
      ip="192.168.1.113",
      tcp_offset=(0, 0, 50, 0, 0, 0),
    )
    await backend.setup()
    self.mock_arm.set_tcp_offset.assert_called_once_with([0, 0, 50, 0, 0, 0])
