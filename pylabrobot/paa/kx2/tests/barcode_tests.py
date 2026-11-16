"""Byte-level scanner commands and cleanup, without a serial connection."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.paa.kx2 import KX2BarcodeReader


def reader() -> KX2BarcodeReader:
  """Create a scanner with a mocked PLR Serial transport."""
  scanner = KX2BarcodeReader("test-port")
  scanner.io.setup = AsyncMock()  # type: ignore[method-assign]
  scanner.io.stop = AsyncMock()  # type: ignore[method-assign]
  scanner.io.write = AsyncMock()  # type: ignore[method-assign]
  scanner.io.reset_input_buffer = AsyncMock()  # type: ignore[method-assign]
  scanner.io.read = AsyncMock(return_value=b"FW 1.0\r")  # type: ignore[method-assign]
  return scanner


class BarcodeTests(unittest.IsolatedAsyncioTestCase):
  """Verify trigger cleanup and preserve scanner configuration."""

  async def test_version_scan_and_close_exact_bytes(self) -> None:
    """Send only Z1, Z and Y; leave mode and firmware read-window settings alone."""
    scanner = reader()
    await scanner.setup()
    scanner.io.read = AsyncMock(return_value=b"PLATE123\r")  # type: ignore[method-assign]
    barcode = await scanner.scan_barcode()
    self.assertIsNotNone(barcode)
    assert barcode is not None
    self.assertEqual(barcode.data, "PLATE123")
    await scanner.stop()
    self.assertEqual(
      [call.args[0] for call in scanner.io.write.call_args_list],  # type: ignore[attr-defined]
      [b"\x1bZ1\r\n", b"\x1bZ\r\n", b"\x1bY\r\n"],
    )
    scanner.io.stop.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_scan_timeout_turns_trigger_off(self) -> None:
    """A no-read returns None and still sends trigger-off."""
    scanner = reader()
    await scanner.setup()
    scanner.io.read = AsyncMock(return_value=b"")  # type: ignore[method-assign]
    self.assertIsNone(await scanner.scan_barcode(read_time=0.001))
    scanner.io.write.assert_awaited_with(b"\x1bY\r\n")  # type: ignore[attr-defined]
    await scanner.stop()

  async def test_stop_joins_scan_cleanup_and_closes(self) -> None:
    """Teardown interrupts an active scan and waits for trigger-off before closing."""
    scanner = reader()
    await scanner.setup()
    started = asyncio.Event()

    async def read_wait(num_bytes: int = 1) -> bytes:
      """Block until stop cancels the scan."""
      started.set()
      await asyncio.Future()
      return b""

    scanner.io.read = read_wait  # type: ignore[method-assign]
    task = asyncio.create_task(scanner.scan_barcode())
    await asyncio.wait_for(started.wait(), 2)
    await scanner.stop()
    self.assertTrue(task.cancelled())
    scanner.io.write.assert_awaited_with(b"\x1bY\r\n")  # type: ignore[attr-defined]
    scanner.io.stop.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_setup_failure_closes_port(self) -> None:
    """A bad version reply cannot leak the serial connection."""
    scanner = reader()
    scanner.io.read = AsyncMock(side_effect=OSError("read failed"))  # type: ignore[method-assign]
    with self.assertRaises(OSError):
      await scanner.setup()
    scanner.io.stop.assert_awaited_once()  # type: ignore[attr-defined]

  async def test_buffer_retains_next_line(self) -> None:
    """Two replies in one serial read remain separately readable."""
    scanner = reader()
    scanner.io.read = AsyncMock(return_value=b"ONE\rTWO\r")  # type: ignore[method-assign]
    self.assertEqual(await scanner._read_line(1), "ONE")
    self.assertEqual(await scanner._read_line(1), "TWO")
    scanner.io.read.assert_awaited_once()  # type: ignore[attr-defined]
