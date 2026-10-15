import logging

import anyio

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.barcode_scanners.backend import (
  BarcodeScannerBackend,
  BarcodeScannerError,
)
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KeyenceBarcodeScannerBackend(BarcodeScannerBackend):
  default_baudrate = 9600
  serial_messaging_encoding = "ascii"
  init_timeout = 1.0  # seconds
  poll_interval = 0.2  # seconds
  reply_gap = 1.0  # seconds of silence after which the reply is complete

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
      rtscts=False,
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    await self.initialize()

  async def initialize(self):
    """Initialize the Keyence barcode scanner."""

    try:
      with anyio.fail_after(self.init_timeout):
        while True:
          response = await self.send_command("RMOTOR")
          if response.strip() == "MOTORON":
            logger.info("Barcode scanner motor is ON.")
            return
          if response.strip() == "MOTOROFF":
            raise BarcodeScannerError("Failed to initialize Keyence barcode scanner: Motor is off.")
          await anyio.sleep(self.poll_interval)
    except TimeoutError as error:
      raise BarcodeScannerError(
        "Failed to initialize Keyence barcode scanner: Timeout waiting for motor to turn on."
      ) from error

  async def send_command(self, command: str) -> str:
    """Send a command and return its reply, accumulated byte-by-byte up to the \r terminator.

    Replies are bare \r-terminated and silence is a valid empty reply (e.g. no barcode)."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    buf = bytearray()
    while True:
      with anyio.move_on_after(self.reply_gap) as gap:
        chunk = await self.io.read()
      if gap.cancel_called:
        break
      buf.extend(chunk)
      if chunk == b"\r":
        break
    return buf.decode(self.serial_messaging_encoding).strip()

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
      with anyio.CancelScope(shield=True):
        try:
          await self.send_command("LOFF")
        except Exception:
          logger.warning("Failed to turn off barcode reader beam (LOFF)", exc_info=True)
