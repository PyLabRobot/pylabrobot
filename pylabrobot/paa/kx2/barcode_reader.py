"""The KX2 serial barcode reader, without changes to its stored scanner configuration."""

import asyncio
import logging
import math
import time
from typing import Optional

from pylabrobot.io.canopen import finish_io
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KX2BarcodeReaderError(RuntimeError):
  """A malformed reply or an unavailable barcode reader."""


class KX2BarcodeReader:
  """An independently connected reader using ESC commands and CR-terminated replies.

  The reader must be configured for external triggering. Setup, scanning and teardown leave
  its mode, symbologies and firmware read window unchanged. ``read_time`` is a host deadline;
  the reader can finish its own read window earlier. A scan always switches its trigger off.
  """

  default_baudrate = 9600

  def __init__(self, port: str, baudrate: int = default_baudrate) -> None:
    """Configure a serial connection without opening it."""
    self.io = Serial(
      human_readable_device_name="KX2 Barcode Reader",
      port=port,
      baudrate=baudrate,
      bytesize=8,
      parity="N",
      stopbits=1,
      write_timeout=1,
      timeout=0.1,
      rtscts=False,
    )
    self._lock = asyncio.Lock()
    self._connected = False
    self._buffer = bytearray()
    self._scan_task: Optional[asyncio.Task] = None
    self._stopping = False

  async def _send_command(self, command: str) -> None:
    """Send the manufacturer's ESC + ASCII + CR LF frame."""
    await self.io.write(b"\x1b" + command.encode("ascii") + b"\r\n")

  async def _read_line(self, timeout: float) -> str:
    """Read one CR-terminated reply, preserving any following bytes."""
    deadline = time.monotonic() + timeout
    while True:
      if b"\r" in self._buffer:
        line, _, tail = self._buffer.partition(b"\r")
        self._buffer[:] = tail
        return line.decode("ascii", errors="strict").lstrip("\x1b\n").rstrip()
      if time.monotonic() >= deadline:
        raise TimeoutError("KX2 barcode reader did not reply before the deadline")
      self._buffer.extend(await self.io.read(32))
      if len(self._buffer) > 65536:
        raise KX2BarcodeReaderError("Barcode reply exceeds 64 KiB without a terminator")
      await asyncio.sleep(0)

  async def _request_version(self) -> str:
    """Query Z1 without changing scanner settings."""
    await self._send_command("Z1")
    version = await self._read_line(5.0)
    if not version:
      raise KX2BarcodeReaderError("Empty software-version response")
    return version

  async def setup(self) -> None:
    """Connect and verify the version; close the port after a partial setup failure."""
    async with self._lock:
      if self._connected:
        return
      self._stopping = False
      self._buffer.clear()
      try:
        await self.io.setup()
        version = await self._request_version()
      except BaseException:
        await finish_io(asyncio.ensure_future(self.io.stop()))
        raise
      self._connected = True
      logger.warning(
        "KX2 barcode reader: version queries and empty scans tested on BD01J09; "
        "successful barcode decoding remains unverified"
      )
      logger.info("KX2 barcode reader connected: version=%s", version)

  async def request_software_version(self) -> str:
    """Read the scanner firmware version."""
    async with self._lock:
      if not self._connected or self._stopping:
        raise KX2BarcodeReaderError("Barcode reader is not connected")
      return await self._request_version()

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    """Return one barcode or None by the host deadline, preserving the firmware read window."""
    timeout = 10.0 if read_time is None else read_time
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("read_time must be finite and positive")
    async with self._lock:
      if not self._connected or self._stopping:
        raise KX2BarcodeReaderError("Barcode reader is not connected")
      self._scan_task = asyncio.current_task()
      try:
        self._buffer.clear()
        await self.io.reset_input_buffer()
        try:
          await self._send_command("Z")
          try:
            data = await self._read_line(timeout)
          except TimeoutError:
            return None
          return (
            Barcode(data=data, symbology="ANY 1D", position_on_resource="front") if data else None
          )
        finally:
          await finish_io(asyncio.ensure_future(self._send_command("Y")))
      finally:
        self._scan_task = None

  async def _join_scan(self, task: asyncio.Task) -> None:
    """Join trigger cleanup without turning task cancellation into a stop failure."""
    try:
      await task
    except asyncio.CancelledError:
      pass

  async def _close(self) -> None:
    """Close after pending scanner operations release the port."""
    async with self._lock:
      try:
        if self._connected:
          await self.io.stop()
      finally:
        self._connected = False
        self._buffer.clear()

  async def stop(self) -> None:
    """Join any active scan's trigger cleanup and close the port."""
    self._stopping = True
    try:
      task = self._scan_task
      if task is not None and task is not asyncio.current_task():
        task.cancel()
        try:
          await finish_io(asyncio.ensure_future(self._join_scan(task)))
        except asyncio.CancelledError:
          pass
    finally:
      await finish_io(asyncio.ensure_future(self._close()))
