import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.iswap import iSWAP
from pylabrobot.resources import Coordinate


class TestiSWAPCommands(unittest.IsolatedAsyncioTestCase):
  """Test that iSWAP methods produce the exact same firmware commands as the legacy
  STARBackend equivalents."""

  async def asyncSetUp(self):
    self.mock_driver = MagicMock()
    self.mock_driver.send_command = AsyncMock()
    self.iswap = iSWAP(driver=self.mock_driver)

  async def test_pick_up_at_location(self):
    """C0PPid0001xs03479xd0yj1142yd0zj1874zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"""
    await self.iswap.pick_up_at_location(
      location=Coordinate(347.9, 114.2, 187.4),
      direction=0.0,
      resource_width=127.76,
    )

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PP",
      xs="03479",
      xd=0,
      yj="1142",
      yd=0,
      zj="1874",
      zd=0,
      gr=1,
      th="2800",
      te="2800",
      gw=4,
      go="1308",
      gb="1245",
      gt="20",
      ga=0,
      gc=False,
    )

  async def test_pick_up_grip_direction_left(self):
    """C0PPid0003xs10427xd0yj3286yd0zj2063zd0gr4th2800te2800gw4go1308gb1245gt20ga0gc0"""
    await self.iswap.pick_up_at_location(
      location=Coordinate(1042.7, 328.6, 206.3),
      direction=270.0,
      resource_width=127.76,
    )

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PP",
      xs="10427",
      xd=0,
      yj="3286",
      yd=0,
      zj="2063",
      zd=0,
      gr=4,
      th="2800",
      te="2800",
      gw=4,
      go="1308",
      gb="1245",
      gt="20",
      ga=0,
      gc=False,
    )

  async def test_drop_at_location(self):
    """C0PRid0002xs03479xd0yj3062yd0zj1874zd0th2800te2800gr1go1308ga0gc0"""
    await self.iswap.drop_at_location(
      location=Coordinate(347.9, 306.2, 187.4),
      direction=0.0,
      resource_width=127.76,
    )

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PR",
      xs="03479",
      xd=0,
      yj="3062",
      yd=0,
      zj="1874",
      zd=0,
      th="2800",
      te="2800",
      gr=1,
      go="1308",
      ga=0,
      gc=False,
    )

  async def test_drop_grip_direction_left(self):
    """C0PRid0002xs10427xd0yj3286yd0zj2063zd0th2800te2800gr4go1308ga0gc0"""
    await self.iswap.drop_at_location(
      location=Coordinate(1042.7, 328.6, 206.3),
      direction=270.0,
      resource_width=127.76,
    )

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PR",
      xs="10427",
      xd=0,
      yj="3286",
      yd=0,
      zj="2063",
      zd=0,
      th="2800",
      te="2800",
      gr=4,
      go="1308",
      ga=0,
      gc=False,
    )

  async def test_park(self):
    await self.iswap.park()

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PG",
      th=2800,
    )
    self.assertTrue(self.iswap.parked)

  async def test_park_custom_height(self):
    await self.iswap.park(backend_params=iSWAP.ParkParams(minimum_traverse_height=200.0))

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="PG",
      th=2000,
    )

  async def test_open_gripper(self):
    await self.iswap.open_gripper(gripper_width=130.8)

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="GF",
      go="1308",
    )

  async def test_close_gripper(self):
    await self.iswap.close_gripper(
      gripper_width=86.0,
      backend_params=iSWAP.CloseGripperParams(grip_strength=5, plate_width_tolerance=0),
    )

    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="GC",
      gw=5,
      gb="0860",
      gt="00",
    )

  async def test_is_gripper_closed(self):
    self.mock_driver.send_command.return_value = {"ph": 1}
    result = await self.iswap.is_gripper_closed()
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0",
      command="QP",
      fmt="ph#",
    )

  async def test_is_gripper_open(self):
    self.mock_driver.send_command.return_value = {"ph": 0}
    result = await self.iswap.is_gripper_closed()
    self.assertFalse(result)

  async def test_parked_state_after_pick(self):
    await self.iswap.pick_up_at_location(
      location=Coordinate(100, 100, 100),
      direction=0.0,
      resource_width=80.0,
    )
    self.assertFalse(self.iswap.parked)

  async def test_parked_state_after_drop(self):
    await self.iswap.drop_at_location(
      location=Coordinate(100, 100, 100),
      direction=0.0,
      resource_width=80.0,
    )
    self.assertFalse(self.iswap.parked)
