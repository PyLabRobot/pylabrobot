"""Legacy. Use pylabrobot.capabilities.barcode_scanning instead."""

from pylabrobot.legacy.barcode_scanners.backend import BarcodeScannerBackend
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources.barcode import Barcode


class BarcodeScanner(Machine):
  """Legacy standalone barcode scanner Machine.

  In new code, use BarcodeScanningCapability instead.
  """

  def __init__(self, backend: BarcodeScannerBackend):
    super().__init__(backend=backend)
    self.backend: BarcodeScannerBackend = backend

  async def scan(self) -> Barcode:
    """Scan a barcode and return its value."""
    return await self.backend.scan_barcode()
