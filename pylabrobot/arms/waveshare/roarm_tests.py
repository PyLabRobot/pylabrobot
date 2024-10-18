
import unittest
from unittest.mock import AsyncMock, patch


from pylabrobot.arms import RoboticArm, WaveshareArm

class TestRoboticArm(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.waveshare_backend = WaveshareArm(host="example.com", port=80)
    self.arm = RoboticArm(backend=self.waveshare_backend)
    await self.arm.setup()

  async def asyncTearDown(self):
    await self.arm.stop()

  @patch('asyncio.open_connection')
  async def test_setup(self, mock_open_connection):
    mock_reader = AsyncMock()
    mock_writer = AsyncMock()
    mock_open_connection.return_value = (mock_reader, mock_writer)
    await self.arm.setup()
    self.assertTrue(self.arm.setup_finished)
    # Assert that the connection was opened
    mock_open_connection.assert_called_once_with('example.com', 80)


  @patch('asyncio.open_connection')
  async def test_stop(self, mock_open_connection):
    mock_reader = AsyncMock()
    mock_writer = AsyncMock()
    mock_open_connection.return_value = (mock_reader, mock_writer)
    await self.arm.setup()
    await self.arm.stop()
    self.assertFalse(self.arm.setup_finished)
    # Assert that the connection was opened
    mock_open_connection.assert_called_once_with('example.com', 80)

    # Assert that the writer was closed after writing
    mock_writer.close.assert_called_once()

    # Reopen the connection so the teardown doesn't fail
    await self.arm.setup()

  @patch('asyncio.open_connection')
  async def test_send_command(self, mock_open_connection):
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b'{"success": true, "data": "some_value"}'
    mock_writer = AsyncMock()
    mock_open_connection.return_value = (mock_reader, mock_writer)
    await self.arm.setup()
    await self.arm.send_command({'command': 'move'})

    # Assert that the writer was closed after writing
    mock_writer.write.assert_called_once_with(b'{"command": "move"}')

  @patch('asyncio.open_connection')
  async def test_move(self, mock_open_connection):
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b'{"success": true}'
    mock_writer = AsyncMock()
    mock_open_connection.return_value = (mock_reader, mock_writer)
    await self.arm.setup()
    await self.arm.move(1, 2, 3, 4)
    # Assert that the writer was closed after writing
    expected_call = b'{"type": "move_xyzt", "x": 1, "y": 2, "z": 3, "grip_angle": 4}'
    mock_writer.write.assert_called_once_with(expected_call)

  @patch('asyncio.open_connection')
  async def test_move_interpolate(self, mock_open_connection):
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b'{"success": true}'
    mock_writer = AsyncMock()
    mock_open_connection.return_value = (mock_reader, mock_writer)
    await self.arm.setup()
    await self.arm.move_interpolate(1, 2, 3, 4, 5)
    # Assert that the writer was closed after writing
    test_dict = b'{"type": "move_xyzt_interp", "x": 1, "y": 2, "z": 3, "grip_angle": 4, "speed": 5}'
    mock_writer.write.assert_called_once_with(test_dict)

if __name__ == '__main__':
    unittest.main()
