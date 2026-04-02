import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.thermo_fisher.multidrop_combi.driver import MultidropCombiDriver
from pylabrobot.thermo_fisher.multidrop_combi.errors import (
  MultidropCombiCommunicationError,
  MultidropCombiInstrumentError,
)


def _make_driver() -> MultidropCombiDriver:
  """Create a driver with a mock Serial for testing."""
  driver = MultidropCombiDriver(port="COM3")
  mock_io = MagicMock()
  mock_io.write = AsyncMock()
  mock_io.readline = AsyncMock()
  mock_io.reset_input_buffer = AsyncMock()
  mock_io.reset_output_buffer = AsyncMock()

  # Mock the timeout API
  _timeout = 30.0

  def get_read_timeout():
    return _timeout

  def set_read_timeout(t):
    nonlocal _timeout
    _timeout = t

  mock_io.get_read_timeout = get_read_timeout
  mock_io.set_read_timeout = set_read_timeout
  mock_io.temporary_timeout = lambda t: contextlib.contextmanager(
    lambda: (mock_io.set_read_timeout(t), (yield), mock_io.set_read_timeout(_timeout))  # type: ignore[arg-type, misc]
  )()

  # Use the real temporary_timeout from Serial for correct behavior
  @contextlib.contextmanager
  def _temporary_timeout(timeout):
    original = mock_io.get_read_timeout()
    mock_io.set_read_timeout(timeout)
    try:
      yield
    finally:
      mock_io.set_read_timeout(original)

  mock_io.temporary_timeout = _temporary_timeout

  driver.io = mock_io
  driver._command_lock = asyncio.Lock()
  return driver


class SendCommandTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.driver = _make_driver()

  async def test_simple_command(self) -> None:
    """Test a simple command with echo + END response."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"SPL\r\n",
      b"SPL END 0\r\n",
    ]
    result = await self.driver.send_command("SPL 1")
    self.assertEqual(result, [])
    self.driver.io.write.assert_awaited_once_with(b"SPL 1\r")  # type: ignore[attr-defined]

  async def test_command_with_data_lines(self) -> None:
    """Test a command that returns data lines between echo and END."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"VER\r\n",
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    result = await self.driver.send_command("VER")
    self.assertEqual(result, ["MultidropCombi 2.00.29 836-4191"])

  async def test_command_with_error_status(self) -> None:
    """Test that non-zero status raises MultidropCombiInstrumentError."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"SPL\r\n",
      b"SPL END 18\r\n",
    ]
    with self.assertRaises(MultidropCombiInstrumentError) as ctx:
      await self.driver.send_command("SPL 99")
    self.assertEqual(ctx.exception.status_code, 18)
    self.assertIn("Invalid plate type", ctx.exception.description)

  async def test_timeout_raises_communication_error(self) -> None:
    """Test that timeout (empty readline) raises MultidropCombiCommunicationError."""
    self.driver.io.readline.side_effect = [b""]  # type: ignore[attr-defined]
    with self.assertRaises(MultidropCombiCommunicationError):
      await self.driver.send_command("SPL 1")

  async def test_not_connected(self) -> None:
    """Test that sending a command when not set up raises error."""
    self.driver._command_lock = None
    with self.assertRaises(MultidropCombiCommunicationError):
      await self.driver.send_command("VER")

  async def test_custom_timeout(self) -> None:
    """Test that custom timeout is set during command and restored after."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"POU\r\n",
      b"POU END 0\r\n",
    ]
    original = self.driver.io.get_read_timeout()
    await self.driver.send_command("POU", timeout=10.0)
    self.assertEqual(self.driver.io.get_read_timeout(), original)

  async def test_echo_skipping_case_insensitive(self) -> None:
    """Test that echo is skipped regardless of case."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"ver\r\n",
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    result = await self.driver.send_command("VER")
    self.assertEqual(result, ["MultidropCombi 2.00.29 836-4191"])


class EnterRemoteModeTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.driver = _make_driver()

  async def test_enter_remote_mode_success(self) -> None:
    """Test successful VER command parses instrument info."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"VER\r\n",
      b"MultidropCombi 2.00.29 836-4191\r\n",
      b"VER END 0\r\n",
    ]
    info = await self.driver._enter_remote_mode()
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
        b"VER\r\n",
        b"VER END 1\r\n",
        b"EAK\r\n",
        b"EAK END 0\r\n",
        b"VER\r\n",
        b"MultidropCombi 2.00.29 836-4191\r\n",
        b"VER END 0\r\n",
      ]
      if call_count <= len(responses):
        return responses[call_count - 1]
      return b""

    self.driver.io.readline.side_effect = readline_side_effect  # type: ignore[attr-defined]
    info = await self.driver._enter_remote_mode()
    self.assertEqual(info["instrument_name"], "MultidropCombi")


class DrainStaleDataTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.driver = _make_driver()

  async def test_drain_with_stale_data(self) -> None:
    """Test draining stale data from buffer."""
    self.driver.io.readline.side_effect = [  # type: ignore[attr-defined]
      b"stale line 1\r\n",
      b"stale line 2\r\n",
      b"",
    ]
    await self.driver._drain_stale_data()
    self.driver.io.reset_input_buffer.assert_awaited_once()  # type: ignore[attr-defined]
    self.driver.io.reset_output_buffer.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_drain_empty_buffer(self) -> None:
    """Test draining when buffer is already empty."""
    self.driver.io.readline.side_effect = [b""]  # type: ignore[attr-defined]
    await self.driver._drain_stale_data()
