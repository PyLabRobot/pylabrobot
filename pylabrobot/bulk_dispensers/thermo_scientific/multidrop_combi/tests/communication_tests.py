import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.communication import (
  MultidropCombiCommunicationMixin,
)
from pylabrobot.bulk_dispensers.thermo_scientific.multidrop_combi.errors import (
  MultidropCombiCommunicationError,
  MultidropCombiInstrumentError,
)


class MockCommunicationBackend(MultidropCombiCommunicationMixin):
  """Testable class that uses the communication mixin with a mock Serial."""

  def __init__(self) -> None:
    self.io: Any = MagicMock()
    self.io._ser = MagicMock()
    self.io._ser.timeout = 30.0
    self._command_lock = asyncio.Lock()

    # Make io.write and io.readline async
    self.io.write = AsyncMock()
    self.io.readline = AsyncMock()
    self.io.reset_input_buffer = AsyncMock()
    self.io.reset_output_buffer = AsyncMock()


class SendCommandTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = MockCommunicationBackend()

  async def test_simple_command(self) -> None:
    """Test a simple command with echo + END response."""
    self.backend.io.readline.side_effect = [
      b"SPL\r\n",           # echo
      b"SPL END 0\r\n",     # end with status 0
    ]
    result = await self.backend._send_command("SPL 1")
    self.assertEqual(result, [])
    self.backend.io.write.assert_awaited_once_with(b"SPL 1\r")

  async def test_command_with_data_lines(self) -> None:
    """Test a command that returns data lines between echo and END."""
    self.backend.io.readline.side_effect = [
      b"VER\r\n",
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    result = await self.backend._send_command("VER")
    self.assertEqual(result, ["MultidropCombi 2.00.29 836-4191"])

  async def test_command_with_error_status(self) -> None:
    """Test that non-zero status raises MultidropCombiInstrumentError."""
    self.backend.io.readline.side_effect = [
      b"SPL\r\n",
      b"SPL END 18\r\n",  # status 18 = Invalid plate type
    ]
    with self.assertRaises(MultidropCombiInstrumentError) as ctx:
      await self.backend._send_command("SPL 99")
    self.assertEqual(ctx.exception.status_code, 18)
    self.assertIn("Invalid plate type", ctx.exception.description)

  async def test_timeout_raises_communication_error(self) -> None:
    """Test that timeout (empty readline) raises MultidropCombiCommunicationError."""
    self.backend.io.readline.side_effect = [b""]
    with self.assertRaises(MultidropCombiCommunicationError):
      await self.backend._send_command("SPL 1")

  async def test_not_connected(self) -> None:
    """Test that sending a command when io is None raises error."""
    self.backend.io = None
    with self.assertRaises(MultidropCombiCommunicationError):
      await self.backend._send_command("VER")

  async def test_custom_timeout(self) -> None:
    """Test that custom timeout is set and restored."""
    self.backend.io.readline.side_effect = [
      b"POU\r\n",
      b"POU END 0\r\n",
    ]
    original = self.backend.io._ser.timeout
    await self.backend._send_command("POU", timeout=10.0)
    # Timeout should be restored after command
    self.assertEqual(self.backend.io._ser.timeout, original)

  async def test_echo_skipping_case_insensitive(self) -> None:
    """Test that echo is skipped regardless of case."""
    self.backend.io.readline.side_effect = [
      b"ver\r\n",  # lowercase echo
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    result = await self.backend._send_command("VER")
    self.assertEqual(result, ["MultidropCombi 2.00.29 836-4191"])


class EnterRemoteModeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = MockCommunicationBackend()

  async def test_enter_remote_mode_success(self) -> None:
    """Test successful VER command parses instrument info."""
    self.backend.io.readline.side_effect = [
      b"VER\r\n",
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    info = await self.backend._enter_remote_mode()
    self.assertEqual(info["instrument_name"], "MultidropCombi")
    self.assertEqual(info["firmware_version"], "2.00.29")
    self.assertEqual(info["serial_number"], "836-4191")

  async def test_enter_remote_mode_retry_after_eak(self) -> None:
    """Test VER retry after EAK when first VER fails."""
    call_count = 0

    async def readline_side_effect() -> bytes:
      nonlocal call_count
      call_count += 1
      responses = [
        # First VER attempt - fails with error
        b"VER\r\n",
        b"VER END 1\r\n",
        # EAK attempt
        b"EAK\r\n",
        b"EAK END 0\r\n",
        # Second VER attempt - succeeds
        b"VER\r\n",
        b"MultidropCombi 2.00.29 836-4191\r\n",
        b"VER END 0\r\n",
      ]
      if call_count <= len(responses):
        return responses[call_count - 1]
      return b""

    self.backend.io.readline.side_effect = readline_side_effect
    info = await self.backend._enter_remote_mode()
    self.assertEqual(info["instrument_name"], "MultidropCombi")


class DrainStaleDataTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.backend = MockCommunicationBackend()

  async def test_drain_with_stale_data(self) -> None:
    """Test draining stale data from buffer."""
    self.backend.io.readline.side_effect = [
      b"stale line 1\r\n",
      b"stale line 2\r\n",
      b"",  # No more data
    ]
    await self.backend._drain_stale_data()
    self.backend.io.reset_input_buffer.assert_awaited_once()
    self.backend.io.reset_output_buffer.assert_awaited_once()

  async def test_drain_empty_buffer(self) -> None:
    """Test draining when buffer is already empty."""
    self.backend.io.readline.side_effect = [b""]
    await self.backend._drain_stale_data()
