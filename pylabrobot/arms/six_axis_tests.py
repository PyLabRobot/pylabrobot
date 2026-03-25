import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.arms.six_axis import SixAxisArm
from pylabrobot.arms.six_axis_backend import SixAxisBackend
from pylabrobot.arms.standard import CartesianCoords
from pylabrobot.resources import Coordinate, Rotation


class TestSixAxisArm(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_backend = MagicMock(spec=SixAxisBackend)
    for method_name in [
      "move_to",
      "get_joint_position",
      "get_cartesian_position",
      "open_gripper",
      "close_gripper",
      "halt",
      "home",
      "move_to_safe",
      "approach",
      "pick_up_resource",
      "drop_resource",
      "freedrive_mode",
      "end_freedrive_mode",
    ]:
      setattr(self.mock_backend, method_name, AsyncMock())
    self.arm = SixAxisArm(backend=self.mock_backend)

  async def test_move_to(self):
    position = CartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.arm.move_to(position)
    self.mock_backend.move_to.assert_called_once_with(position)

  async def test_get_joint_position(self):
    await self.arm.get_joint_position()
    self.mock_backend.get_joint_position.assert_called_once()

  async def test_get_cartesian_position(self):
    await self.arm.get_cartesian_position()
    self.mock_backend.get_cartesian_position.assert_called_once()

  async def test_open_gripper(self):
    await self.arm.open_gripper(position=850, speed=5)
    self.mock_backend.open_gripper.assert_called_once_with(position=850, speed=5)

  async def test_close_gripper(self):
    await self.arm.close_gripper(position=100, speed=5)
    self.mock_backend.close_gripper.assert_called_once_with(position=100, speed=5)

  async def test_halt(self):
    await self.arm.halt()
    self.mock_backend.halt.assert_called_once()

  async def test_home(self):
    await self.arm.home()
    self.mock_backend.home.assert_called_once()

  async def test_move_to_safe(self):
    await self.arm.move_to_safe()
    self.mock_backend.move_to_safe.assert_called_once()

  async def test_approach(self):
    position = CartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.arm.approach(position)
    self.mock_backend.approach.assert_called_once_with(position, access=None)

  async def test_pick_up_resource(self):
    position = CartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.arm.pick_up_resource(position)
    self.mock_backend.pick_up_resource.assert_called_once_with(
      position=position, access=None
    )

  async def test_drop_resource(self):
    position = CartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.arm.drop_resource(position)
    self.mock_backend.drop_resource.assert_called_once_with(position, access=None)

  async def test_freedrive_mode(self):
    await self.arm.freedrive_mode()
    self.mock_backend.freedrive_mode.assert_called_once()
    self.assertTrue(self.arm._in_freedrive)

  async def test_end_freedrive_mode(self):
    self.arm._in_freedrive = True
    await self.arm.end_freedrive_mode()
    self.mock_backend.end_freedrive_mode.assert_called_once()
    self.assertFalse(self.arm._in_freedrive)

  async def test_auto_exit_freedrive_on_move(self):
    self.arm._in_freedrive = True
    position = CartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.arm.move_to(position)
    self.mock_backend.end_freedrive_mode.assert_called_once()
    self.assertFalse(self.arm._in_freedrive)
    self.mock_backend.move_to.assert_called_once_with(position)
