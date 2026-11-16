"""Byte-level scanner commands and cleanup, without a serial connection."""

from typing import Any, Tuple

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.serial import Serial
from pylabrobot.paa.kx2 import KX2BarcodeReader, KX2BarcodeReaderError
from pylabrobot.testing import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO


class FakeSerial(Serial):
  """In-memory ``Serial`` double backed by ``MockIO``."""

  def __init__(self) -> None:
    super().__init__(human_readable_device_name="Fake KX2 Barcode Reader", port="test-port")
    self.is_open = False
    self.close_count = 0
    self.reset_count = 0
    self.mock_io = MockIO()
    self.mock_io.read.side_effect = b"FW 1.0\r"

  def _close_port(self) -> None:
    self.is_open = False
    self.close_count += 1

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    self.is_open = True
    stack.callback(self._close_port)

  async def write(self, data: bytes, *args: Any, **kwargs: Any) -> int:
    await self.mock_io.write(data, *args, **kwargs)
    return len(data)

  async def reset_input_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.reset_count += 1

  async def read(self, num_bytes: int = 1, *args: Any, **kwargs: Any) -> bytes:
    return await self.mock_io.read(num_bytes, *args, **kwargs)


def reader() -> Tuple[KX2BarcodeReader, FakeSerial]:
  """Create a scanner with a lifespan-managed Serial double."""
  scanner = KX2BarcodeReader("test-port")
  serial_io = FakeSerial()
  scanner.io = serial_io
  return scanner, serial_io


class TestBarcodeReader(AnyioTestBase):
  """Verify trigger cleanup and preserve scanner configuration."""

  async def test_version_scan_and_close_exact_bytes(self) -> None:
    """Send only Z1, Z and Y; leave mode and firmware read-window settings alone."""
    scanner, serial_io = reader()
    async with scanner:
      serial_io.mock_io.read.side_effect = b"PLATE123\r"
      barcode = await scanner.scan_barcode()
      assert barcode is not None
      assert barcode.data == "PLATE123"
    assert [args[0] for args, _ in serial_io.mock_io.write.calls] == [
      b"\x1bZ1\r\n",
      b"\x1bZ\r\n",
      b"\x1bY\r\n",
    ]
    assert serial_io.close_count == 1
    assert not serial_io.is_open

  async def test_scan_timeout_turns_trigger_off(self) -> None:
    """A no-read returns None and still sends trigger-off."""
    scanner, serial_io = reader()
    async with scanner:
      serial_io.mock_io.read.side_effect = None
      assert await scanner.scan_barcode(read_time=0.01) is None
      assert serial_io.mock_io.write.calls[-1][0] == (b"\x1bY\r\n",)
    assert serial_io.close_count == 1

  async def test_eof_raises_barcode_reader_error_and_turns_trigger_off(self) -> None:
    """EOF during a scan raises KX2BarcodeReaderError after switching trigger off."""
    scanner, serial_io = reader()
    async with scanner:
      serial_io.mock_io.read.side_effect = anyio.EndOfStream()
      with pytest.raises(KX2BarcodeReaderError, match="disconnected"):
        await scanner.scan_barcode()
      assert serial_io.mock_io.write.calls[-1][0] == (b"\x1bY\r\n",)

  async def test_stop_joins_scan_cleanup_and_closes(self) -> None:
    """Teardown interrupts an active scan and waits for trigger-off before closing."""
    scanner, serial_io = reader()
    started = anyio.Event()
    interrupted = anyio.Event()

    async def read_wait(num_bytes: int = 1) -> bytes:
      """Block until lifespan exit cancels the scan."""
      del num_bytes
      started.set()
      await anyio.sleep_forever()
      return b""

    async def run_scan() -> None:
      with pytest.raises(KX2BarcodeReaderError, match="interrupted"):
        await scanner.scan_barcode()
      interrupted.set()

    with anyio.fail_after(5):
      async with anyio.create_task_group() as tg:
        async with scanner:
          serial_io.mock_io.read.side_effect = read_wait
          tg.start_soon(run_scan)
          await started.wait()
        assert interrupted.is_set()
        assert serial_io.mock_io.write.calls[-1][0] == (b"\x1bY\r\n",)
        assert serial_io.close_count == 1
        assert not serial_io.is_open

  async def test_simultaneous_outer_cancellation_and_shutdown_propagates_cancellation(self) -> None:
    """Concurrent caller cancellation is not converted into KX2BarcodeReaderError."""
    scanner, serial_io = reader()
    started = anyio.Event()
    caller_scope = anyio.CancelScope()

    async def read_wait(num_bytes: int = 1) -> bytes:
      del num_bytes
      started.set()
      await anyio.sleep_forever()
      return b""

    async def run_scan() -> None:
      with caller_scope:
        await scanner.scan_barcode()

    with anyio.fail_after(5):
      async with anyio.create_task_group() as tg:
        async with scanner:
          serial_io.mock_io.read.side_effect = read_wait
          tg.start_soon(run_scan)
          await started.wait()
          caller_scope.cancel()
        assert caller_scope.cancelled_caught

  async def test_setup_failure_closes_port(self) -> None:
    """A bad version reply cannot leak the serial connection."""
    scanner, serial_io = reader()
    serial_io.mock_io.read.side_effect = OSError("read failed")
    with pytest.raises(OSError, match="read failed"):
      async with scanner:
        pass
    assert serial_io.close_count == 1
    assert not serial_io.is_open

  async def test_buffer_retains_next_line(self) -> None:
    """Two replies in one serial read remain separately readable."""
    scanner, serial_io = reader()
    serial_io.mock_io.read.side_effect = [b"ONE\rTWO\r"]
    assert await scanner._read_line(1) == "ONE"
    assert await scanner._read_line(1) == "TWO"
    assert len(serial_io.mock_io.read.calls) == 1
