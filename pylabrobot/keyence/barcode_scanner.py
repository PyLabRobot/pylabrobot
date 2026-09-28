import logging
import sys
from typing import Optional

import anyio

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.serial import Serial
from pylabrobot.resources.barcode import Barcode

logger = logging.getLogger(__name__)


class KeyenceBarcodeScannerError(Exception):
  """Base exception for Keyence barcode scanner errors."""


class KeyenceBarcodeScanner(HasLifespan):
  """Keyence barcode scanner (BL-600HA, BL-1300)."""

  serial_messaging_encoding = "ascii"
  default_baudrate = 9600
  init_timeout = 1.0
  poll_interval = 0.2
  reply_gap = 1.0

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
      rtscts=False,
    )

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
  ) -> None:
    """Own the serial transport and initialize the scanner."""
    await super()._enter_lifespan(stack)
    await stack.enter_async_context(self.io.lifespan())
    logger.info("[Keyence %s] connected", self.io.port)

    try:
      with anyio.fail_after(self.init_timeout):
        while True:
          response = await self.send_command("RMOTOR")
          if response.strip() == "MOTORON":
            logger.info("[Keyence %s] barcode scanner motor is ON", self.io.port)
            return
          if response.strip() == "MOTOROFF":
            raise KeyenceBarcodeScannerError(
              "Failed to initialize Keyence barcode scanner: Motor is off."
            )
          await anyio.sleep(self.poll_interval)
    except TimeoutError as error:
      raise KeyenceBarcodeScannerError(
        "Failed to initialize Keyence barcode scanner: Timeout waiting for motor to turn on."
      ) from error

  async def send_command(self, command: str) -> str:
    """Send a command to the barcode scanner and return the response.
    Keyence uses carriage return \\r as the line ending by default."""

    await self.io.write((command + "\r").encode(self.serial_messaging_encoding))
    response = bytearray()
    while True:
      with anyio.move_on_after(self.reply_gap) as gap:
        chunk = await self.io.read()
      if gap.cancel_called:
        break
      response.extend(chunk)
      if b"\r" in response:
        break
    decoded = response.decode(self.serial_messaging_encoding).strip()
    return decoded

  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    # Keyence BL-series LON command doesn't take a read window — the scanner
    # uses its own configured read mode/timeout. Accept and ignore for
    # capability-API compatibility.
    del read_time
    scan_error: Optional[BaseException] = None
    try:
      data = await self.send_command("LON")
      if data.startswith("NG"):
        logger.error("[Keyence %s] barcode reader is off: cannot read barcode", self.io.port)
        raise KeyenceBarcodeScannerError("Barcode reader is off: cannot read barcode")
      if data.startswith("ERR99"):
        logger.error("[Keyence %s] barcode reader error: %s", self.io.port, data)
        raise KeyenceBarcodeScannerError(f"Error response from barcode reader: {data}")
      logger.info("[Keyence %s] scanned barcode: %s", self.io.port, data)
      return Barcode(data=data, symbology="unknown", position_on_resource="front")
    except BaseException as error:
      scan_error = error
      raise
    finally:
      try:
        await self._turn_off_beam()
      except anyio.get_cancelled_exc_class() as cleanup_error:
        if scan_error is not None:
          raise BaseExceptionGroup(
            "Barcode scan and beam cleanup both failed",
            [scan_error, cleanup_error],
          ) from None
        raise

  async def _turn_off_beam(self) -> None:
    """Release the scanner beam without masking the scan outcome."""
    with anyio.CancelScope(shield=True):
      try:
        await self.send_command("LOFF")
      except anyio.get_cancelled_exc_class():
        logger.warning(
          "[Keyence %s] Failed to turn off barcode reader beam (LOFF)",
          self.io.port,
          exc_info=True,
        )
        raise
      except Exception:
        logger.warning(
          "[Keyence %s] Failed to turn off barcode reader beam (LOFF)",
          self.io.port,
          exc_info=True,
        )
