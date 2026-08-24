import asyncio
import logging
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Union
from unittest import mock

from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.io import capture as capture_module
from pylabrobot.io import ftdi as ftdi_module
from pylabrobot.io.capture import CaptureReader, capturer
from pylabrobot.io.ftdi import FTDI, HAS_PYLIBFTDI, HAS_PYUSB, FTDICommand, FTDIValidator
from pylabrobot.io.validation_utils import LOG_LEVEL_IO


class _RecordingHandler(logging.Handler):
  """Captures every record emitted to a logger, whatever method produced it - so the
  test cannot be fooled by swapping logger.log for logger.debug/info."""

  def __init__(self) -> None:
    super().__init__(level=LOG_LEVEL_IO)
    self.records: list[logging.LogRecord] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.records.append(record)


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDIEmptyReadTests(unittest.IsolatedAsyncioTestCase):
  """Empty reads must be neither logged nor captured (mirrors io.serial). Polling a quiet
  device reads b'' repeatedly; logging/capturing each one floods the log and capture.
  Locks the `if len(data) != 0:` guard on read()/readline() - asserting on real log
  records (not a mocked method) so re-routing the log call can't fake a pass."""

  def setUp(self) -> None:
    self._handler = _RecordingHandler()
    self._logger = logging.getLogger("pylabrobot.io.ftdi")
    self._prev_level = self._logger.level
    self._logger.setLevel(LOG_LEVEL_IO)
    self._logger.addHandler(self._handler)
    self.addCleanup(self._logger.setLevel, self._prev_level)
    self.addCleanup(self._logger.removeHandler, self._handler)

  def _ftdi(self, return_value: bytes) -> FTDI:
    # Bypass setup() (no hardware) by driving the underlying device read directly.
    dev = FTDI(human_readable_device_name="test", device_id="test")
    dev._dev = mock.Mock()
    dev._dev.read.return_value = return_value
    dev._dev.readline.return_value = return_value
    return dev

  async def test_empty_read_is_not_logged_or_captured(self) -> None:
    dev = self._ftdi(b"")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      self.assertEqual(await dev.read(4), b"")
    self.assertEqual(self._handler.records, [])
    mock_record.assert_not_called()

  async def test_empty_readline_is_not_logged_or_captured(self) -> None:
    dev = self._ftdi(b"")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      with self.assertRaises(TimeoutError):
        await dev.readline(timeout=0.05)
    self.assertEqual(self._handler.records, [])
    mock_record.assert_not_called()

  async def test_nonempty_read_is_logged_and_captured(self) -> None:
    dev = self._ftdi(b"\x01\x02")
    with mock.patch.object(ftdi_module.capturer, "record") as mock_record:
      self.assertEqual(await dev.read(2), b"\x01\x02")
    self.assertEqual(len(self._handler.records), 1)
    self.assertIn(str(b"\x01\x02"), self._handler.records[0].getMessage())
    mock_record.assert_called_once()


class _BlockingDevice:
  """A pylibftdi device stand-in whose calls block, standing in for a bulk transfer.

  Args:
    to_read: bytes the device hands out, oldest first.
    block_s: how long every call sleeps before returning.
  """

  def __init__(self, to_read: bytes = b"", block_s: float = 0.0, text_mode: bool = False) -> None:
    self._to_read = bytearray(to_read)
    self._block_s = block_s
    self._text_mode = text_mode
    self.written = bytearray()
    self.reads = 0
    self.ftdi_fn = mock.Mock()

  def read(self, num_bytes: int) -> Union[bytes, str]:
    data = bytes(self._to_read[:num_bytes])
    del self._to_read[:num_bytes]
    self.reads += 1
    time.sleep(self._block_s)
    # pylibftdi decodes with latin-1 when the device was opened in text mode.
    return data.decode("latin-1") if self._text_mode else data

  def write(self, data: bytes) -> int:
    time.sleep(self._block_s)
    self.written.extend(data)
    return len(data)


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDICancellationTests(unittest.IsolatedAsyncioTestCase):
  """Running read/write on the executor makes them cancellable for the first time, and a call
  already handed to the worker cannot be recalled. Locks the guarantee that neither end loses
  data when its caller goes away: a cancelled read's bytes are already out of the chip's buffer,
  so dropping them would leave the next read part-way through a response."""

  def _ftdi(self, device: _BlockingDevice) -> FTDI:
    # Bypass setup() (no hardware) by driving the underlying device directly.
    io = FTDI(human_readable_device_name="test", device_id="test")
    io._dev = device
    io._executor = ThreadPoolExecutor(max_workers=1)
    self.addCleanup(io._executor.shutdown)
    return io

  async def test_cancelled_read_keeps_its_bytes_for_the_next_read(self) -> None:
    device = _BlockingDevice(to_read=b"ABCD", block_s=0.2)
    io = self._ftdi(device)

    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(io.read(4), timeout=0.05)

    self.assertEqual(await io.read(4), b"ABCD")
    # The second read waited for the one in flight rather than issuing another.
    self.assertEqual(device.reads, 1)

  async def test_cancelled_read_is_captured_once(self) -> None:
    device = _BlockingDevice(to_read=b"ABCD", block_s=0.2)
    io = self._ftdi(device)
    records: List[Any] = []

    with mock.patch.object(ftdi_module.capturer, "record", records.append):
      with self.assertRaises(asyncio.TimeoutError):
        await asyncio.wait_for(io.read(4), timeout=0.05)
      await io.read(4)

    self.assertEqual([(r.action, r.data) for r in records], [("read", "41424344")])

  async def test_cancelled_write_still_reaches_the_device(self) -> None:
    device = _BlockingDevice(block_s=0.2)
    io = self._ftdi(device)
    records: List[Any] = []

    with mock.patch.object(ftdi_module.capturer, "record", records.append):
      with self.assertRaises(asyncio.TimeoutError):
        await asyncio.wait_for(io.write(b"\x01\x02"), timeout=0.05)
      await asyncio.sleep(0.3)  # let the worker finish

    self.assertEqual(bytes(device.written), b"\x01\x02")
    self.assertEqual([(r.action, r.data) for r in records], [("write", "0102")])

  async def test_purge_discards_bytes_held_for_the_next_read(self) -> None:
    device = _BlockingDevice(to_read=b"ABCD", block_s=0.2)
    io = self._ftdi(device)

    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(io.read(4), timeout=0.05)
    await io.usb_purge_rx_buffer()

    # A purge means the caller has decided everything in flight is stale.
    self.assertEqual(await io.read(4), b"")

  async def test_a_text_mode_device_still_reads(self) -> None:
    """pylibftdi returns str, decoded with latin-1, if the device was opened in text mode. setup()
    opens it in byte mode, so this only guards against losing the tolerance upstream had."""
    io = self._ftdi(_BlockingDevice(to_read=b"\xff\x01", text_mode=True))

    self.assertEqual(await io.read(2), b"\xff\x01")

  async def test_second_concurrent_reader_gets_an_empty_read(self) -> None:
    """Two callers on one io stay unsupported, but the second one now backs off rather than
    taking bytes out of the middle of the first one's response."""
    io = self._ftdi(_BlockingDevice(to_read=b"ABCD", block_s=0.01))
    received: dict = {"first": [], "second": []}

    async def reader(name: str) -> None:
      for _ in range(4):
        received[name].append(await io.read(1))

    await asyncio.gather(reader("first"), reader("second"))

    self.assertEqual(received["first"], [b"A", b"B", b"C", b"D"])
    self.assertEqual(received["second"], [b"", b"", b"", b""])


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDIEventTests(unittest.IsolatedAsyncioTestCase):
  """`read` now returns from a buffer and `write` is shielded, so both `emit_event` sites moved."""

  async def test_read_and_write_still_emit_io_events(self) -> None:
    io = FTDI(human_readable_device_name="test", device_id="test")
    io._dev = _BlockingDevice(to_read=b"\x03\x04")
    events: List[Any] = []
    bus = EventBus()
    bus.subscribe(events.append)

    with use_event_bus(bus):
      await io.write(b"\x01\x02")
      await io.read(2)

    self.assertEqual(
      [(event.name, event.data["data"]) for event in events],
      [("io.write", "0102"), ("io.read", "0304")],
    )


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDICaptureTests(unittest.IsolatedAsyncioTestCase):
  """A capture keeps its payload, so FTDIValidator can replay it. FTDICommand declared `data`
  without assigning it, so every recorded FTDI command was written out without its bytes."""

  def setUp(self) -> None:
    # CaptureReader.done() leaves the capture/validation flag set, which blocks the next FTDI().
    self.addCleanup(setattr, capture_module, "_capture_or_validation_active", False)

  def test_command_carries_its_data(self) -> None:
    # The capture file is written from __dict__, so an unassigned field is a lost payload.
    command = FTDICommand(device_id="test", action="write", data="dead")
    self.assertEqual(command.__dict__["data"], "dead")

  async def test_capture_replays_through_the_validator(self) -> None:
    io = FTDI(human_readable_device_name="test", device_id="test")
    io._dev = _BlockingDevice(to_read=b"\x03\x04")

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      capturer.start(path)
      self.addCleanup(lambda: capturer.capture_active and capturer.stop())
      await io.set_baudrate(115200)
      await io.write(b"\x01\x02")
      await io.read(2)
      capturer.stop()

      reader = CaptureReader(str(path))
      validator = FTDIValidator(reader, "test", "test")
      await validator.set_baudrate(115200)
      await validator.write(b"\x01\x02")
      self.assertEqual(await validator.read(2), b"\x03\x04")
      reader.done()


@unittest.skipUnless(HAS_PYLIBFTDI and HAS_PYUSB, "pylibftdi/pyusb not installed")
class FTDIReadlineTests(unittest.IsolatedAsyncioTestCase):
  """pylibftdi's readline raises TypeError unless the device was opened in text mode, and setup()
  opens it in byte mode, so the line is assembled here instead."""

  def _ftdi(self, to_read: bytes) -> FTDI:
    io = FTDI(human_readable_device_name="test", device_id="test")
    io._dev = _BlockingDevice(to_read=to_read)
    return io

  async def test_line_is_returned_with_its_terminator(self) -> None:
    io = self._ftdi(b"ok\ntrailing")
    self.assertEqual(await io.readline(), b"ok\n")
    self.assertEqual(await io.readline(terminator=b"g"), b"trailing")

  async def test_incomplete_line_times_out(self) -> None:
    io = self._ftdi(b"partial")
    with self.assertRaises(TimeoutError):
      await io.readline(timeout=0.05)

  async def test_empty_terminator_is_rejected(self) -> None:
    io = self._ftdi(b"")
    with self.assertRaises(ValueError):
      await io.readline(terminator=b"")


if __name__ == "__main__":
  unittest.main()
