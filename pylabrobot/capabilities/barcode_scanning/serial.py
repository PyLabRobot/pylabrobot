import asyncio
import logging
from typing import Optional, Sequence

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode, BarcodePosition

from .backend import BarcodeScannerBackend
from .barcode_scanning import BarcodeScanner

logger = logging.getLogger(__name__)


class SerialBarcodeScannerDriver(Driver):
  """Line-oriented serial driver for barcode scanners.

  This driver is intended for scanners configured as RS-232 or USB virtual COM
  devices. It reads bytes from :class:`pylabrobot.io.Serial` until a configured
  line terminator is seen.
  """

  def __init__(
    self,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    baudrate: int = 9600,
    bytesize: int = 8,
    parity: str = "N",
    stopbits: int = 1,
    write_timeout: float = 1,
    read_timeout: float = 1,
    rtscts: bool = False,
    dsrdtr: bool = False,
    xonxoff: bool = False,
    encoding: str = "utf-8",
    terminators: Sequence[bytes] = (b"\r", b"\n"),
    max_line_length: int = 4096,
  ):
    super().__init__()
    if len(terminators) == 0:
      raise ValueError("At least one line terminator must be configured.")
    if any(len(t) != 1 for t in terminators):
      raise ValueError("SerialBarcodeScannerDriver only supports one-byte terminators.")
    if max_line_length <= 0:
      raise ValueError("max_line_length must be positive.")

    self.io = Serial(
      human_readable_device_name="Serial Barcode Scanner",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
      write_timeout=write_timeout,
      timeout=read_timeout,
      rtscts=rtscts,
      dsrdtr=dsrdtr,
      xonxoff=xonxoff,
    )
    self.encoding = encoding
    self.terminators = tuple(terminators)
    self.max_line_length = max_line_length

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
    await self.io.setup()
    logger.info("[Serial barcode scanner %s] connected", self.io.port)

  async def stop(self):
    await self.io.stop()
    logger.info("[Serial barcode scanner %s] disconnected", self.io.port)

  async def read_line(self, timeout: Optional[float] = None) -> str:
    """Read one barcode line from the serial stream.

    Args:
      timeout: Optional total read timeout in seconds. If omitted, the
        underlying :class:`pylabrobot.io.Serial` timeout is used.

    Returns:
      The decoded line without the trailing line terminator. Returns an empty
      string if the timeout elapses before any byte is read.
    """
    raw = await self._read_until_terminator(timeout=timeout)
    while any(raw.endswith(terminator) for terminator in self.terminators):
      raw = raw[:-1]
    return raw.decode(self.encoding, errors="replace")

  async def write(self, data: bytes):
    """Write raw bytes to the scanner."""
    await self.io.write(data)

  async def _read_until_terminator(self, timeout: Optional[float]) -> bytes:
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    buf = bytearray()

    while len(buf) < self.max_line_length:
      if deadline is None:
        chunk = await self.io.read(1)
      else:
        remaining = deadline - loop.time()
        if remaining <= 0:
          break
        with self.io.temporary_timeout(remaining):
          chunk = await self.io.read(1)

      if len(chunk) == 0:
        break
      buf.extend(chunk)
      if bytes(chunk) in self.terminators:
        break

    return bytes(buf)

  async def reset_input_buffer(self):
    """Clear unread bytes buffered by the serial transport."""
    await self.io.reset_input_buffer()


class SerialBarcodeScannerBackend(BarcodeScannerBackend):
  """Barcode-scanning backend for line-oriented serial scanners."""

  def __init__(
    self,
    driver: SerialBarcodeScannerDriver,
    symbology: str = "unknown",
    position_on_resource: BarcodePosition = "front",
    trigger_command: Optional[bytes] = None,
    untrigger_command: Optional[bytes] = None,
  ):
    super().__init__()
    self.driver = driver
    self.symbology = symbology
    self.position_on_resource = position_on_resource
    self.trigger_command = trigger_command
    self.untrigger_command = untrigger_command

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    if read_time is not None and read_time < 0:
      raise ValueError("read_time must be non-negative.")

    if self.trigger_command is not None:
      await self.driver.write(self.trigger_command)

    try:
      data = await self.driver.read_line(timeout=read_time)
    finally:
      if self.untrigger_command is not None:
        await self.driver.write(self.untrigger_command)

    if data == "":
      return None

    logger.info("[Serial barcode scanner %s] scanned barcode: %s", self.driver.io.port, data)
    return Barcode(
      data=data,
      symbology=self.symbology,
      position_on_resource=self.position_on_resource,
    )


class SerialBarcodeScanner(Device):
  """Barcode scanner connected through RS-232 or USB virtual COM."""

  def __init__(
    self,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    baudrate: int = 9600,
    bytesize: int = 8,
    parity: str = "N",
    stopbits: int = 1,
    write_timeout: float = 1,
    read_timeout: float = 1,
    rtscts: bool = False,
    dsrdtr: bool = False,
    xonxoff: bool = False,
    encoding: str = "utf-8",
    terminators: Sequence[bytes] = (b"\r", b"\n"),
    max_line_length: int = 4096,
    symbology: str = "unknown",
    position_on_resource: BarcodePosition = "front",
    trigger_command: Optional[bytes] = None,
    untrigger_command: Optional[bytes] = None,
  ):
    driver = SerialBarcodeScannerDriver(
      port=port,
      vid=vid,
      pid=pid,
      baudrate=baudrate,
      bytesize=bytesize,
      parity=parity,
      stopbits=stopbits,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      rtscts=rtscts,
      dsrdtr=dsrdtr,
      xonxoff=xonxoff,
      encoding=encoding,
      terminators=terminators,
      max_line_length=max_line_length,
    )
    super().__init__(driver=driver)
    self.driver: SerialBarcodeScannerDriver = driver
    self.barcode_scanning = BarcodeScanner(
      backend=SerialBarcodeScannerBackend(
        driver=driver,
        symbology=symbology,
        position_on_resource=position_on_resource,
        trigger_command=trigger_command,
        untrigger_command=untrigger_command,
      )
    )
    self._capabilities = [self.barcode_scanning]
