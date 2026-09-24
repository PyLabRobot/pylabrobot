import asyncio
import threading
import unittest
from types import SimpleNamespace
from typing import Any, Callable, Coroutine, Optional
from unittest import mock

from pylabrobot.io import ftdi as ftdi_module
from pylabrobot.io import hid as hid_module
from pylabrobot.io import serial as serial_module
from pylabrobot.io import usb as usb_module


class _ClosableDevice:
  def __init__(self) -> None:
    self.setup_thread: Optional[int] = None
    self.close_thread: Optional[int] = None

  def close(self) -> None:
    self.close_thread = threading.get_ident()


class _DisposableDevice:
  def __init__(self) -> None:
    self.setup_thread: Optional[int] = None
    self.dispose_thread: Optional[int] = None

  def set_configuration(self) -> None:
    self.setup_thread = threading.get_ident()
    raise RuntimeError("configuration failed")


class ExecutorSetupTests(unittest.IsolatedAsyncioTestCase):
  async def _wait_for_task(self, task: "asyncio.Task[None]") -> None:
    while not task.done():
      await asyncio.wait({task}, timeout=0.01)
    await task

  async def _cancel_while_worker_is_running(
    self,
    setup: Callable[[], Coroutine[Any, Any, None]],
    started: threading.Event,
    release: threading.Event,
  ) -> None:
    task: "asyncio.Task[None]" = asyncio.create_task(setup())
    while not started.is_set():
      await asyncio.sleep(0)

    try:
      task.cancel()
      await asyncio.sleep(0)
      self.assertFalse(task.done(), "setup returned before its worker finished")
    finally:
      release.set()

    with self.assertRaises(asyncio.CancelledError):
      await self._wait_for_task(task)

  async def test_cancelled_ftdi_setup_closes_device_before_executor_shutdown(self) -> None:
    io = ftdi_module.FTDI.__new__(ftdi_module.FTDI)
    io.human_readable_device_name = "mock FTDI"
    io._device_id = "mock"
    io._dev = None
    io._executor = None
    device = _ClosableDevice()
    started = threading.Event()
    release = threading.Event()

    def setup_sync() -> None:
      device.setup_thread = threading.get_ident()
      started.set()
      release.wait()
      io._dev = device  # type: ignore[assignment]

    io._setup_sync = setup_sync  # type: ignore[method-assign]
    ftdi_error = type("MockFtdiError", (Exception,), {})
    with mock.patch.object(ftdi_module, "FtdiError", ftdi_error, create=True):
      await self._cancel_while_worker_is_running(io.setup, started, release)

    self.assertEqual(device.close_thread, device.setup_thread)
    self.assertIsNone(io._dev)
    self.assertIsNone(io._executor)

  async def test_cancelled_hid_setup_closes_device_before_executor_shutdown(self) -> None:
    io = hid_module.HID.__new__(hid_module.HID)
    io.human_readable_device_name = "mock HID"
    io._unique_id = "mock"
    io.device = None
    io._executor = None
    device = _ClosableDevice()
    started = threading.Event()
    release = threading.Event()

    def setup_sync() -> None:
      device.setup_thread = threading.get_ident()
      started.set()
      release.wait()
      io.device = device  # type: ignore[assignment]

    io._setup_sync = setup_sync  # type: ignore[method-assign]
    with mock.patch.object(hid_module, "USE_HID", True):
      await self._cancel_while_worker_is_running(io.setup, started, release)

    self.assertEqual(device.close_thread, device.setup_thread)
    self.assertIsNone(io.device)
    self.assertIsNone(io._executor)

  async def test_cancelled_serial_setup_closes_port_before_executor_shutdown(self) -> None:
    io = serial_module.Serial.__new__(serial_module.Serial)
    io.human_readable_device_name = "mock serial"
    io._ser = None
    io._executor = None
    device = _ClosableDevice()
    started = threading.Event()
    release = threading.Event()

    def setup_sync() -> str:
      device.setup_thread = threading.get_ident()
      started.set()
      release.wait()
      io._ser = device  # type: ignore[assignment]
      return "/dev/mock"

    io._setup_sync = setup_sync  # type: ignore[method-assign]
    with mock.patch.object(serial_module, "HAS_SERIAL", True):
      await self._cancel_while_worker_is_running(io.setup, started, release)

    self.assertEqual(device.close_thread, device.setup_thread)
    self.assertIsNone(io._ser)
    self.assertIsNone(io._executor)

  async def test_cancelled_usb_setup_disposes_device_before_executor_shutdown(self) -> None:
    io = usb_module.USB(
      id_vendor=1,
      id_product=2,
      human_readable_device_name="mock USB",
      packet_read_timeout=1,
      read_timeout=2,
    )
    device = _DisposableDevice()
    started = threading.Event()
    release = threading.Event()

    def setup_sync(empty_buffer: bool) -> None:
      device.setup_thread = threading.get_ident()
      started.set()
      release.wait()
      io.dev = device  # type: ignore[assignment]

    def dispose_resources(dev: object) -> None:
      self.assertIs(dev, device)
      device.dispose_thread = threading.get_ident()

    io._setup_sync = setup_sync  # type: ignore[method-assign]
    fake_usb = SimpleNamespace(util=SimpleNamespace(dispose_resources=dispose_resources))
    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", fake_usb, create=True),
    ):
      await self._cancel_while_worker_is_running(io.setup, started, release)

    self.assertEqual(device.dispose_thread, device.setup_thread)
    self.assertIsNone(io.dev)
    self.assertIsNone(io._read_executor)
    self.assertIsNone(io._write_executor)

  async def test_usb_setup_error_disposes_acquired_device_on_worker(self) -> None:
    io = usb_module.USB(
      id_vendor=1,
      id_product=2,
      human_readable_device_name="mock USB",
      packet_read_timeout=1,
      read_timeout=2,
    )
    device = _DisposableDevice()

    def dispose_resources(dev: object) -> None:
      self.assertIs(dev, device)
      device.dispose_thread = threading.get_ident()

    io.get_available_devices = mock.Mock(return_value=[device])  # type: ignore[method-assign]
    fake_usb = SimpleNamespace(util=SimpleNamespace(dispose_resources=dispose_resources))
    with (
      mock.patch.object(usb_module, "USE_USB", True),
      mock.patch.object(usb_module, "usb", fake_usb, create=True),
      self.assertRaisesRegex(RuntimeError, "configuration failed"),
    ):
      await self._wait_for_task(asyncio.create_task(io.setup()))

    self.assertEqual(device.dispose_thread, device.setup_thread)
    self.assertIsNone(io.dev)
    self.assertIsNone(io._read_executor)
    self.assertIsNone(io._write_executor)
