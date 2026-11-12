import asyncio
import ctypes
import logging
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Union
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


class FTDITopologyLifecycleTests(unittest.IsolatedAsyncioTestCase):
  """Topology selection remains portable and always releases its host resources."""

  @staticmethod
  def _ftdi_without_optional_dependency_checks() -> FTDI:
    io = FTDI.__new__(FTDI)
    io.ftdi_library = "libftdi"
    io._interface_select = None
    io._detached_kernel_driver = None
    io._dev = None
    io._d2xx_dev = None
    io._executor = None
    io._unread = bytearray()
    io._pending_read = None
    return io

  async def test_detach_tolerates_a_backend_without_kernel_driver_support(self) -> None:
    io = self._ftdi_without_optional_dependency_checks()
    device = mock.Mock()
    device.is_kernel_driver_active.side_effect = NotImplementedError
    dispose_resources = mock.Mock()
    usb_module = SimpleNamespace(util=SimpleNamespace(dispose_resources=dispose_resources))

    with (
      mock.patch.object(ftdi_module, "usb", usb_module, create=True),
      mock.patch.object(io, "_usb_device_at", return_value=device),
    ):
      io._detach_kernel_driver(3, 17)

    self.assertIsNone(io._detached_kernel_driver)
    device.detach_kernel_driver.assert_not_called()
    dispose_resources.assert_called_once_with(device)

  async def test_detach_tolerates_an_unsupported_detach_operation(self) -> None:
    io = self._ftdi_without_optional_dependency_checks()
    device = mock.Mock()
    device.is_kernel_driver_active.return_value = True
    device.detach_kernel_driver.side_effect = NotImplementedError
    dispose_resources = mock.Mock()
    usb_module = SimpleNamespace(util=SimpleNamespace(dispose_resources=dispose_resources))

    with (
      mock.patch.object(ftdi_module, "usb", usb_module, create=True),
      mock.patch.object(io, "_usb_device_at", return_value=device),
    ):
      io._detach_kernel_driver(3, 17)

    self.assertIsNone(io._detached_kernel_driver)
    device.detach_kernel_driver.assert_called_once_with(0)
    dispose_resources.assert_called_once_with(device)

  async def test_stop_reattaches_and_shuts_down_after_close_failure(self) -> None:
    io = self._ftdi_without_optional_dependency_checks()
    device = mock.Mock()
    device.close.side_effect = RuntimeError("simulated close failure")
    io._dev = device
    io._executor = ThreadPoolExecutor(max_workers=1)
    io._detached_kernel_driver = (3, 17, 0)

    with (
      mock.patch.object(io, "_reattach_kernel_driver") as reattach,
      self.assertRaisesRegex(RuntimeError, "close failure"),
    ):
      await io.stop()

    reattach.assert_called_once_with()
    self.assertIsNone(io._dev)
    self.assertIsNone(io._executor)

  async def test_cancelled_stop_waits_for_close_and_reattaches(self) -> None:
    io = self._ftdi_without_optional_dependency_checks()
    close_started = threading.Event()

    def close() -> None:
      close_started.set()
      time.sleep(0.05)

    io._dev = SimpleNamespace(close=close)
    io._executor = ThreadPoolExecutor(max_workers=1)
    io._detached_kernel_driver = (3, 17, 0)

    with mock.patch.object(io, "_reattach_kernel_driver") as reattach:
      stop_task = asyncio.create_task(io.stop())
      for _ in range(100):
        if close_started.is_set():
          break
        await asyncio.sleep(0.001)
      self.assertTrue(close_started.is_set())
      stop_task.cancel()
      for _ in range(200):
        if stop_task.done():
          break
        await asyncio.sleep(0.001)
      self.assertTrue(stop_task.done())
      with self.assertRaises(asyncio.CancelledError):
        await stop_task

    reattach.assert_called_once_with()
    self.assertIsNone(io._dev)
    self.assertIsNone(io._executor)


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


class FakeD2XXLibrary:
  """FTDI's D2XX library, answering through the same ctypes arguments: lists ``channels`` as
  (serial number, VID:PID ID, flags), holds bytes queued for reading, and logs each setting."""

  def __init__(self, *, channels: List[Any]):
    self.channels = channels
    self.settings: List[Any] = []
    self.opened: List[str] = []
    self.queued = bytearray()
    self.write_limit: Optional[int] = None

  def FT_CreateDeviceInfoList(self, count):
    count._obj.value = len(self.channels)
    return 0

  def FT_GetDeviceInfoList(self, nodes, count):
    for node, (serial_number, device_id, flags) in zip(nodes, self.channels):
      node.SerialNumber = serial_number.encode("ascii")
      node.ID = device_id
      node.Flags = flags
    count._obj.value = len(self.channels)
    return 0

  def FT_OpenEx(self, serial_number, flags, handle):
    self.opened.append(serial_number.value.decode("ascii"))
    handle._obj.value = 1
    return 0

  def FT_Close(self, handle):
    return 0

  def FT_SetTimeouts(self, handle, read_timeout_ms, write_timeout_ms):
    self.settings.append(("timeouts", read_timeout_ms.value, write_timeout_ms.value))
    return 0

  def FT_SetLatencyTimer(self, handle, latency):
    self.settings.append(("latency", latency.value))
    return 0

  def FT_SetFlowControl(self, handle, flowctrl, xon, xoff):
    self.settings.append(("flow", flowctrl.value, xon.value, xoff.value))
    return 0

  def FT_SetRts(self, handle):
    self.settings.append(("rts", True))
    return 0

  def FT_Write(self, handle, data, count, written):
    written._obj.value = min(count.value, self.write_limit or count.value)
    return 0

  def FT_GetQueueStatus(self, handle, queued):
    queued._obj.value = len(self.queued)
    return 0

  def FT_Read(self, handle, buffer, count, received):
    data = bytes(self.queued[: count.value])
    del self.queued[: count.value]
    ctypes.memmove(buffer, data, len(data))
    received._obj.value = len(data)
    return 0


# One FT2232H chip's two channels as D2XX lists them: serial number plus interface letter.
FT2232H_ID = 0x0403 << 16 | 0x6010
TWO_CHANNEL_CHIP = [("SERIALA", FT2232H_ID, 0), ("SERIALB", FT2232H_ID, 0)]


class FTDID2XXTests(unittest.IsolatedAsyncioTestCase):
  def use_library(self, *, channels: List[Any]) -> FakeD2XXLibrary:
    library = FakeD2XXLibrary(channels=channels)
    patcher = mock.patch.object(ftdi_module, "_d2xx_library", library)
    patcher.start()
    self.addCleanup(patcher.stop)
    return library

  async def set_up_interface_b(self) -> "tuple[FTDI, FakeD2XXLibrary]":
    library = self.use_library(channels=TWO_CHANNEL_CHIP)
    io = FTDI(human_readable_device_name="test", interface_select=2, ftdi_library="d2xx")
    await io.setup()
    self.addAsyncCleanup(io.stop)
    return io, library

  def test_automatic_choice_is_d2xx_only_on_windows_and_only_when_asked(self):
    self.use_library(channels=[])
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
    ):
      for platform, chosen in (("win32", "d2xx"), ("darwin", "libftdi"), ("linux", "libftdi")):
        with (
          self.subTest(platform=platform),
          mock.patch.object(ftdi_module.sys, "platform", platform),
        ):
          automatic = FTDI(human_readable_device_name="test", ftdi_library=None)
          self.assertEqual(automatic.ftdi_library, chosen)
          self.assertEqual(FTDI(human_readable_device_name="test").ftdi_library, "libftdi")

  def test_unknown_library_is_refused(self):
    with self.assertRaisesRegex(ValueError, "ftdi_library must be"):
      FTDI(human_readable_device_name="test", ftdi_library="serial")

  async def test_interface_b_opens_its_channel_and_keeps_the_base_serial(self):
    io, library = await self.set_up_interface_b()
    self.assertEqual(library.opened, ["SERIALB"])
    self.assertEqual(io.device_id, "SERIAL")

  async def test_no_interface_takes_channel_a_as_libftdi_does(self):
    library = self.use_library(channels=TWO_CHANNEL_CHIP)
    io = FTDI(human_readable_device_name="test", device_id="SERIAL", ftdi_library="d2xx")
    await io.setup()
    self.addAsyncCleanup(io.stop)
    self.assertEqual(library.opened, ["SERIALA"])

  async def test_unresolvable_channels_refuse_setup(self):
    for channels, interface_select, usb_address, message in (
      ([], 2, None, "restore FTDI's driver"),
      ([("", FT2232H_ID, 1)], 2, None, "close the vendor software"),
      (TWO_CHANNEL_CHIP + [("OTHERA", FT2232H_ID, 0)], 1, None, "Multiple FTDI channels"),
      (TWO_CHANNEL_CHIP, 2, "3-2", "usb_address is not supported"),
    ):
      with self.subTest(message=message):
        self.use_library(channels=channels)
        io = FTDI(
          human_readable_device_name="test",
          interface_select=interface_select,
          usb_address=usb_address,
          ftdi_library="d2xx",
        )
        with self.assertRaisesRegex((RuntimeError, ValueError), message):
          await io.setup()

  async def test_settings_take_libftdi_values(self):
    io, library = await self.set_up_interface_b()
    await io.set_flowctrl(flowctrl=0x100)
    await io.set_rts(level=True)
    await io.set_latency_timer(latency=2)
    self.assertEqual(
      library.settings,
      [
        ("timeouts", ftdi_module._D2XX_READ_TIMEOUT_MS, ftdi_module._D2XX_WRITE_TIMEOUT_MS),
        ("flow", 0x100, 0x11, 0x13),
        ("rts", True),
        ("latency", 2),
      ],
    )
    with self.assertRaisesRegex(ValueError, "1.5 stop bits"):
      await io.set_line_property(bits=8, stopbits=1, parity=0)

  async def test_read_returns_what_has_arrived_without_waiting(self):
    io, library = await self.set_up_interface_b()
    self.assertEqual(await io.read(num_bytes=16), b"")
    library.queued.extend(b"\x01\x02\x03")
    self.assertEqual(await io.read(num_bytes=2), b"\x01\x02")
    self.assertEqual(await io.read(num_bytes=16), b"\x03")

  async def test_a_short_write_raises(self):
    io, library = await self.set_up_interface_b()
    self.assertEqual(await io.write(data=b"\x01\x02"), 2)
    library.write_limit = 1
    with self.assertRaisesRegex(ftdi_module.D2XXError, "wrote 1 of 2 bytes"):
      await io.write(data=b"\x01\x02")

  async def test_d2xx_setup_is_recorded_and_replays_with_or_without_the_record(self):
    self.use_library(channels=TWO_CHANNEL_CHIP)
    io = FTDI(human_readable_device_name="test", interface_select=2, ftdi_library="d2xx")
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "capture.json"
      capture_module.start_capture(path)
      try:
        await io.setup()
        await io.stop()
      finally:
        capture_module.stop_capture()
      reader = CaptureReader(path=str(path))
    self.assertEqual([(c["action"], c["data"]) for c in reader.commands], [("setup", "d2xx")])
    recorded = list(reader.commands)
    with (
      mock.patch.object(ftdi_module, "HAS_PYLIBFTDI", True),
      mock.patch.object(ftdi_module, "HAS_PYUSB", True),
    ):
      for commands in (recorded, []):
        with self.subTest(recorded=bool(commands)):
          validator = FTDIValidator(
            cr=reader, human_readable_device_name="test", device_id="SERIAL"
          )
          reader.commands = commands
          reader.start()
          try:
            await validator.setup()
          finally:
            reader.done()


if __name__ == "__main__":
  unittest.main()
