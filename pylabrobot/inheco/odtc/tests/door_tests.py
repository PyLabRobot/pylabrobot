"""Tests for ODTCDoorBackend state tracking."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.inheco.odtc.door import DoorStateUnknownError, ODTCDoorBackend
from pylabrobot.inheco.odtc.driver import ODTCDriver


def _make_door_backend() -> ODTCDoorBackend:
  driver = MagicMock(spec=ODTCDriver)
  driver.send_command = AsyncMock(return_value=None)
  return ODTCDoorBackend(driver=driver)


class TestODTCDoorBackend(unittest.IsolatedAsyncioTestCase):
  async def test_initial_state_is_unknown(self):
    door = _make_door_backend()
    with self.assertRaises(DoorStateUnknownError):
      _ = door.is_open

  async def test_state_is_open_after_open(self):
    door = _make_door_backend()
    await door.open()
    self.assertTrue(door.is_open)

  async def test_state_is_closed_after_close(self):
    door = _make_door_backend()
    await door.close()
    self.assertFalse(door.is_open)

  async def test_open_calls_open_door_command(self):
    door = _make_door_backend()
    await door.open()
    door._driver.send_command.assert_called_once_with("OpenDoor")

  async def test_close_calls_close_door_command(self):
    door = _make_door_backend()
    await door.close()
    door._driver.send_command.assert_called_once_with("CloseDoor")

  async def test_state_toggles_correctly(self):
    door = _make_door_backend()
    await door.open()
    self.assertTrue(door.is_open)
    await door.close()
    self.assertFalse(door.is_open)
    await door.open()
    self.assertTrue(door.is_open)

  async def test_on_setup_resets_state_to_unknown(self):
    door = _make_door_backend()
    await door.open()
    self.assertTrue(door.is_open)
    await door._on_setup()
    with self.assertRaises(DoorStateUnknownError):
      _ = door.is_open

  async def test_on_setup_resets_from_closed_to_unknown(self):
    door = _make_door_backend()
    await door.close()
    self.assertFalse(door.is_open)
    await door._on_setup()
    with self.assertRaises(DoorStateUnknownError):
      _ = door.is_open

  async def test_error_message_is_informative(self):
    door = _make_door_backend()
    with self.assertRaises(DoorStateUnknownError) as ctx:
      _ = door.is_open
    self.assertIn("odtc.door.open()", str(ctx.exception))
    self.assertIn("odtc.door.close()", str(ctx.exception))


if __name__ == "__main__":
  unittest.main()
