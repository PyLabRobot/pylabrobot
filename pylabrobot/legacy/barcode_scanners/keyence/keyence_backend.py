"""Legacy. Use pylabrobot.keyence instead."""

from pylabrobot.keyence.keyence_backend import (
  KeyenceBarcodeScannerBarcodeScanningBackend,
  KeyenceBarcodeScannerDriver,
)
from pylabrobot.legacy.barcode_scanners.backend import BarcodeScannerBackend
from pylabrobot.resources.barcode import Barcode


class KeyenceBarcodeScannerBackend(BarcodeScannerBackend):
  """Legacy wrapper around the new Driver + CapabilityBackend.

  In new code, use KeyenceBarcodeScanner (Device) instead.
  """

  def __init__(self, port: str):
    super().__init__()
    self.driver = KeyenceBarcodeScannerDriver(port=port)
    self._barcode_scanning = KeyenceBarcodeScannerBarcodeScanningBackend(self.driver)

  async def setup(self):
    await self.driver.setup()
    await self._barcode_scanning._on_setup()

  async def stop(self):
    await self._barcode_scanning._on_stop()
    await self.driver.stop()

  async def scan_barcode(self) -> Barcode:
    return await self._barcode_scanning.scan_barcode()
