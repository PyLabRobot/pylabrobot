import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.core import CoreGripper
from pylabrobot.resources import Coordinate


class TestCoreGripperCommands(unittest.IsolatedAsyncioTestCase):
  """Test that CoreGripper methods produce the exact same firmware commands as the legacy
  STARBackend equivalents."""

  async def asyncSetUp(self):
    self.mock_interface = MagicMock()
    self.mock_interface.send_command = AsyncMock()
    self.core = CoreGripper(interface=self.mock_interface)

  async def test_pick_up_at_location(self):
    """ZP with default params, plate width 86mm at (347.9, 114.2, 187.4)."""
    await self.core.pick_up_at_location(
      location=Coordinate(347.9, 114.2, 187.4),
      resource_width=86.0,
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZP",
      xs="03479",
      xd=0,
      yj="1142",
      yv="0050",
      zj="1874",
      zy="0500",
      yo="0890",
      yg="0830",
      yw="15",
      th="2800",
      te="2800",
    )

  async def test_pick_up_at_location_custom_params(self):
    """ZP with custom grip strength and speeds."""
    await self.core.pick_up_at_location(
      location=Coordinate(500.0, 200.0, 150.0),
      resource_width=127.76,
      backend_params=CoreGripper.PickUpParams(
        grip_strength=20,
        y_gripping_speed=10.0,
        z_speed=80.0,
        minimum_traverse_height=300.0,
        z_position_at_end=290.0,
      ),
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZP",
      xs="05000",
      xd=0,
      yj="2000",
      yv="0100",
      zj="1500",
      zy="0800",
      yo="1308",
      yg="1248",
      yw="20",
      th="3000",
      te="2900",
    )

  async def test_drop_at_location(self):
    """ZR with default params, plate width 86mm at (347.9, 306.2, 187.4)."""
    await self.core.drop_at_location(
      location=Coordinate(347.9, 306.2, 187.4),
      resource_width=86.0,
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZR",
      xs="03479",
      xd=0,
      yj="3062",
      zj="1874",
      zi="000",
      zy="0500",
      yo="0890",
      th="2800",
      te="2800",
    )

  async def test_drop_at_location_custom_params(self):
    """ZR with custom press distance."""
    await self.core.drop_at_location(
      location=Coordinate(500.0, 200.0, 150.0),
      resource_width=86.0,
      backend_params=CoreGripper.DropParams(
        z_press_on_distance=5.0,
        z_speed=30.0,
        minimum_traverse_height=300.0,
        z_position_at_end=290.0,
      ),
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZR",
      xs="05000",
      xd=0,
      yj="2000",
      zj="1500",
      zi="050",
      zy="0300",
      yo="0890",
      th="3000",
      te="2900",
    )

  async def test_move_to_location(self):
    """ZM with default params at (500.0, 200.0, 150.0)."""
    await self.core.move_to_location(
      location=Coordinate(500.0, 200.0, 150.0),
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZM",
      xs="05000",
      xd=0,
      xg=4,
      yj="2000",
      zj="1500",
      zy="0500",
      th="2800",
    )

  async def test_move_to_location_custom_params(self):
    """ZM with custom acceleration and speed."""
    await self.core.move_to_location(
      location=Coordinate(800.0, 300.0, 200.0),
      backend_params=CoreGripper.MoveToLocationParams(
        acceleration_index=2,
        z_speed=30.0,
        minimum_traverse_height=350.0,
      ),
    )

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZM",
      xs="08000",
      xd=0,
      xg=2,
      yj="3000",
      zj="2000",
      zy="0300",
      th="3500",
    )

  async def test_open_gripper(self):
    """ZO command."""
    await self.core.open_gripper(gripper_width=0)

    self.mock_interface.send_command.assert_called_once_with(
      module="C0",
      command="ZO",
    )
