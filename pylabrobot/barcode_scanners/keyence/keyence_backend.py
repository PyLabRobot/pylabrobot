import logging

import anyio

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
from pylabrobot.concurrency import AsyncExitStackWithShielding
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

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io)
    await self.initialize()

  async def initialize(self):
    """Initialize the Keyence barcode scanner."""
    try:
      with anyio.fail_after(self.init_timeout):
        while True:
          response = await self.send_command("RMOTOR")
          if response.strip() == "MOTORON":
            logger.info("Barcode scanner motor is ON.")
            break
          elif response.strip() == "MOTOROFF":
            raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: Motor is off.")
          await anyio.sleep(self.poll_interval)
    except TimeoutError as e:
      raise BarcodeScannerError(
        "Failed to initialize Keyence barcode scanner: Timeout waiting for motor to turn on."
      ) from e

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = await self.io.read()
    return response.decode(self.serial_messaging_encoding).strip()

  async def scan_barcode(self) -> Barcode:
    data = await self.send_command("LON")
    if data.startswith("NG"):
      raise BarcodeScannerError("Barcode reader is off: cannot read barcode")
    if data.startswith("ERR99"):
      raise BarcodeScannerError(f"Error response from barcode reader: {data}")
    return Barcode(data=data, symbology="unknown", position_on_resource="front")
