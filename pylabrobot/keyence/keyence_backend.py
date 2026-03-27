import asyncio
import logging
import time

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.capabilities.barcode_scanning.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KeyenceBarcodeScannerDriver(Driver):
  """Serial driver for Keyence BL-series barcode scanners.

  Owns the serial connection and provides a generic send_command() method.
  """

  default_baudrate = 9600
  serial_messaging_encoding = "ascii"

  def __init__(self, port: str):
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

  async def stop(self):
    await self.io.stop()

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \\r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = await self.io.read()
    return response.decode(self.serial_messaging_encoding).strip()


class KeyenceBarcodeScannerBarcodeScanningBackend(BarcodeScannerBackend):
  """Translates BarcodeScannerBackend interface into Keyence driver commands."""

  init_timeout = 1.0  # seconds
  poll_interval = 0.2  # seconds

  def __init__(self, driver: KeyenceBarcodeScannerDriver):
    super().__init__()
    self._driver = driver

  async def _on_setup(self):
    """Initialize the barcode scanner motor after the driver connects."""

    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      response = await self._driver.send_command("RMOTOR")
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

  async def scan_barcode(self) -> Barcode:
    data = await self._driver.send_command("LON")
    if data.startswith("NG"):
      raise BarcodeScannerError("Barcode reader is off: cannot read barcode")
    if data.startswith("ERR99"):
      raise BarcodeScannerError(f"Error response from barcode reader: {data}")
    return Barcode(data=data, symbology="unknown", position_on_resource="front")
