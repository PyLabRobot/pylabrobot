import asyncio
import logging
import time

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.barcode_scanners.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KeyenceBarcodeScannerBackend(BarcodeScannerBackend):
  default_baudrate = 9600
  serial_messaging_encoding = "ascii"
  init_timeout = 1.0  # seconds
  poll_interval = 0.2  # seconds

  def __init__(
    self,
    port: str,
  ):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    super().__init__()

    # BL-1300 Barcode reader factory default serial communication settings
    # should be the same factory default for the BL-600HA and BL-1300 models
    self.io = Serial(
      human_readable_device_name="Keyence Barcode Scanner",
      port=port,
      baudrate=self.default_baudrate,
      bytesize=serial.SEVENBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=False,
    )

  async def setup(self):
    await self.io.setup()
    await self.initialize()

  async def initialize(self):
    """Initialize the Keyence barcode scanner."""

    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      response = await self.send_command("RMOTOR")
      if response.strip() == "MOTORON":
        logger.info("Barcode scanner motor is ON.")
        break
      elif response.strip() == "MOTOROFF":
        raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: Motor is off.")
      await asyncio.sleep(self.poll_interval)
    else:
      raise BarcodeScannerError(
        "Failed to initialize Keyence barcode scanner: Timeout waiting for motor to turn on."
      )

  async def send_command(self, command: str) -> str:
    """Send a command and return its reply, accumulated byte-by-byte up to the \r terminator.

    Replies are bare \r-terminated and silence is a valid empty reply (e.g. no barcode), so
    stop at \r or the first byte-less read rather than reading a fixed byte count."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    buf = bytearray()
    while True:
      chunk = await self.io.read()
      if not chunk:
        break  # port timeout elapsed with no byte: reply done, or none coming
      buf.extend(chunk)
      if chunk == b"\r":
        break
    return buf.decode(self.serial_messaging_encoding).strip()

  async def stop(self):
    await self.io.stop()

  async def scan_barcode(self) -> Barcode:
    try:
      data = await self.send_command("LON")
      if data.startswith("NG"):
        raise BarcodeScannerError("Barcode reader is off: cannot read barcode")
      if data.startswith("ERR99"):
        raise BarcodeScannerError(f"Error response from barcode reader: {data}")
      return Barcode(data=data, symbology="unknown", position_on_resource="front")
    finally:
      # LON latches the read beam on; release it whether the read succeeded or raised.
      # try/except so a LOFF failure can't mask the scan's result.
      try:
        await self.send_command("LOFF")
      except Exception:
        logger.warning("Failed to turn off barcode reader beam (LOFF)", exc_info=True)
