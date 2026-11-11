import asyncio
import logging
import time

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from typing import Optional

from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KeyenceBarcodeScannerError(Exception):
  """Base exception for Keyence barcode scanner errors."""


class KeyenceBarcodeScanner:
  """Keyence barcode scanner (BL-600HA, BL-1300)."""

  serial_messaging_encoding = "ascii"
  default_baudrate = 9600
  init_timeout = 1.0
  poll_interval = 0.2

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
    logger.info("[Keyence %s] connected", self.io.port)

    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      response = await self.send_command("RMOTOR")
      if response.strip() == "MOTORON":
        logger.info("[Keyence %s] barcode scanner motor is ON", self.io.port)
        break
      elif response.strip() == "MOTOROFF":
        raise KeyenceBarcodeScannerError(
          "Failed to initialize Keyence barcode scanner: Motor is off."
        )
      await asyncio.sleep(self.poll_interval)
    else:
      raise KeyenceBarcodeScannerError(
        "Failed to initialize Keyence barcode scanner: Timeout waiting for motor to turn on."
      )

  async def stop(self):
    await self.io.stop()
    logger.info("[Keyence %s] disconnected", self.io.port)

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \\r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = await self.io.read()
    decoded = response.decode(self.serial_messaging_encoding).strip()
    return decoded

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    # Keyence BL-series LON command doesn't take a read window — the scanner
    # uses its own configured read mode/timeout. Accept and ignore for
    # capability-API compatibility.
    del read_time
    data = await self.send_command("LON")
    if data.startswith("NG"):
      logger.error("[Keyence %s] barcode reader is off: cannot read barcode", self.io.port)
      raise KeyenceBarcodeScannerError("Barcode reader is off: cannot read barcode")
    if data.startswith("ERR99"):
      logger.error("[Keyence %s] barcode reader error: %s", self.io.port, data)
      raise KeyenceBarcodeScannerError(f"Error response from barcode reader: {data}")
    logger.info("[Keyence %s] scanned barcode: %s", self.io.port, data)
    return Barcode(data=data, symbology="unknown", position_on_resource="front")
