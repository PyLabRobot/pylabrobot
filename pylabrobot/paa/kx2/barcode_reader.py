"""The KX2 serial barcode reader, without changes to its stored scanner configuration."""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KX2BarcodeReaderError(RuntimeError):
  """A malformed reply or an unavailable barcode reader."""


@dataclass
class _ActiveScan:
  """Cancellation and completion handle for an in-flight barcode scan."""

  scope: anyio.CancelScope
  done: anyio.Event = field(default_factory=anyio.Event)


class KX2BarcodeReader(HasLifespan):
  """An independently connected reader using ESC commands and CR-terminated replies.

  The reader must be configured for external triggering. Lifespan entry, scanning and teardown
  leave its mode, symbologies and firmware read window unchanged. ``read_time`` is a host
  deadline; the reader can finish its own read window earlier. A scan always switches its
  trigger off.
  """

  default_baudrate = 9600

  def __init__(self, port: str, baudrate: int = default_baudrate) -> None:
    """Configure a serial connection without opening it."""
    super().__init__()
    self.io = Serial(
      human_readable_device_name="KX2 Barcode Reader",
      port=port,
      baudrate=baudrate,
      bytesize=8,
      parity="N",
      stopbits=1,
      write_timeout=1,
      rtscts=False,
    )
    self._lock = anyio.Lock()
    self._connected = False
    self._buffer = bytearray()
    self._active_scan: Optional[_ActiveScan] = None
    self._stopping = False

  async def _send_command(self, command: str) -> None:
    """Send the manufacturer's ESC + ASCII + CR LF frame."""
    await self.io.write(b"\x1b" + command.encode("ascii") + b"\r\n")

  async def _read_line(self, timeout: float) -> str:
    """Read one CR-terminated reply, preserving any following bytes."""
    with anyio.fail_after(timeout):
      while True:
        if b"\r" in self._buffer:
          line, _, tail = self._buffer.partition(b"\r")
          self._buffer[:] = tail
          return line.decode("ascii", errors="strict").lstrip("\x1b\n").rstrip()
        try:
          chunk = await self.io.read(32)
        except anyio.EndOfStream as exc:
          raise KX2BarcodeReaderError("Barcode reader disconnected") from exc
        if not chunk:
          raise KX2BarcodeReaderError("Barcode reader disconnected")
        self._buffer.extend(chunk)
        if len(self._buffer) > 65536:
          raise KX2BarcodeReaderError("Barcode reply exceeds 64 KiB without a terminator")

  async def _request_version(self) -> str:
    """Query Z1 without changing scanner settings."""
    await self._send_command("Z1")
    version = await self._read_line(5.0)
    if not version:
      raise KX2BarcodeReaderError("Empty software-version response")
    return version

  def _reset_state(self) -> None:
    """Clear connection state after the serial port has closed."""
    self._connected = False
    self._stopping = False
    self._active_scan = None
    self._buffer.clear()

  async def _abort_active_scan(self) -> None:
    """Interrupt any active scan and wait for its trigger-off write before closing."""
    self._stopping = True
    active = self._active_scan
    if active is not None:
      active.scope.cancel()
      await active.done.wait()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Connect and verify the version; close the port after a partial setup failure."""
    await super()._enter_lifespan(stack)
    self._stopping = False
    self._active_scan = None
    self._buffer.clear()
    stack.callback(self._reset_state)
    await stack.enter_async_context(self.io.lifespan())
    stack.push_shielded_async_callback(self._abort_active_scan, shield_timeout=5.0)
    version = await self._request_version()
    self._connected = True
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
      active = _ActiveScan(scope=anyio.CancelScope())
      self._active_scan = active
      try:
        with active.scope:
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
            with anyio.CancelScope(shield=True):
              await self._send_command("Y")
      finally:
        self._active_scan = None
        active.done.set()
      await anyio.lowlevel.checkpoint_if_cancelled()
      raise KX2BarcodeReaderError("Barcode scan interrupted by reader shutdown")
