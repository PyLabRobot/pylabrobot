import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.hamilton.liquid_handlers.star.cover import STARCover


class TestSTARCoverCommands(unittest.IsolatedAsyncioTestCase):
  """Test that STARCover methods produce the exact firmware commands expected."""

  async def asyncSetUp(self):
    self.mock_driver = MagicMock()
    self.mock_driver.send_command = AsyncMock()
    self.cover = STARCover(driver=self.mock_driver)

  async def test_lock(self):
    await self.cover.lock()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="CO")

  async def test_unlock(self):
    await self.cover.unlock()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="HO")

  async def test_disable(self):
    await self.cover.disable()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="CD")

  async def test_enable(self):
    await self.cover.enable()
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="CE")

  async def test_set_output(self):
    await self.cover.set_output(output=1)
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="OS", on=1)

  async def test_set_output_reserve(self):
    await self.cover.set_output(output=2)
    self.mock_driver.send_command.assert_called_once_with(module="C0", command="OS", on=2)

  async def test_set_output_invalid(self):
    with self.assertRaises(AssertionError):
      await self.cover.set_output(output=0)
    with self.assertRaises(AssertionError):
      await self.cover.set_output(output=4)

  async def test_reset_output(self):
    await self.cover.reset_output(output=1)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="QS", on=1, fmt="#"
    )

  async def test_reset_output_invalid(self):
    with self.assertRaises(AssertionError):
      await self.cover.reset_output(output=0)
    with self.assertRaises(AssertionError):
      await self.cover.reset_output(output=4)

  async def test_is_open_true(self):
    self.mock_driver.send_command.return_value = {"qc": 1}
    result = await self.cover.is_open()
    self.assertTrue(result)
    self.mock_driver.send_command.assert_called_once_with(
      module="C0", command="QC", fmt="qc#"
    )

  async def test_is_open_false(self):
    self.mock_driver.send_command.return_value = {"qc": 0}
    result = await self.cover.is_open()
    self.assertFalse(result)
