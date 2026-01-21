import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.arms.backend import SCARABackend
from pylabrobot.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.arms.scara import ExperimentalSCARA
from pylabrobot.resources import Coordinate, Rotation


class TestExperimentalSCARA(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mock_backend = MagicMock(spec=SCARABackend)
    for method_name in [
      "move_to",
      "get_joint_position",
      "get_cartesian_position",
      "open_gripper",
      "close_gripper",
      "is_gripper_closed",
      "halt",
      "home",
      "move_to_safe",
      "approach",
      "pick_up_resource",
      "drop_resource",
    ]:
      setattr(self.mock_backend, method_name, AsyncMock())
    self.scara = ExperimentalSCARA(backend=self.mock_backend)

  async def test_move_to(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.move_to(position)
    self.mock_backend.move_to.assert_called_once_with(position)

  async def test_get_joint_position(self):
    await self.scara.get_joint_position()
    self.mock_backend.get_joint_position.assert_called_once()

  async def test_get_cartesian_position(self):
    await self.scara.get_cartesian_position()
    self.mock_backend.get_cartesian_position.assert_called_once()

  async def test_open_gripper(self):
    gripper_width = 50.0
    await self.scara.open_gripper(gripper_width=gripper_width)
    self.mock_backend.open_gripper.assert_called_once_with(gripper_width=gripper_width)

  async def test_close_gripper(self):
    gripper_width = 50.0
    await self.scara.close_gripper(gripper_width=gripper_width)
    self.mock_backend.close_gripper.assert_called_once_with(gripper_width=gripper_width)

  async def test_is_gripper_closed(self):
    await self.scara.is_gripper_closed()
    self.mock_backend.is_gripper_closed.assert_called_once()

  async def test_halt(self):
    await self.scara.halt()
    self.mock_backend.halt.assert_called_once()

  async def test_home(self):
    await self.scara.home()
    self.mock_backend.home.assert_called_once()

  async def test_move_to_safe(self):
    await self.scara.move_to_safe()
    self.mock_backend.move_to_safe.assert_called_once()

  async def test_approach(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.approach(position)
    self.mock_backend.approach.assert_called_once_with(position, access=None)

  async def test_pick_up_resource(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    plate_width = 10.0
    await self.scara.pick_up_resource(position, plate_width)
    self.mock_backend.pick_up_resource.assert_called_once_with(
      plate_width=plate_width, position=position, access=None
    )

  async def test_drop_resource(self):
    position = PreciseFlexCartesianCoords(
      location=Coordinate(x=100, y=200, z=300), rotation=Rotation(x=0, y=0, z=0)
    )
    await self.scara.drop_resource(position)
    self.mock_backend.drop_resource.assert_called_once_with(position, access=None)
