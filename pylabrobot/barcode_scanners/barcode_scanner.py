from pylabrobot.barcode_scanners.backend import BarcodeScannerBackend
from pylabrobot.machines.machine import Machine
from pylabrobot.resources.barcode import Barcode


class BarcodeScanner(Machine):
  """Frontend for barcode scanners."""

  def __init__(self, backend: BarcodeScannerBackend):
    super().__init__(backend=backend)
    self.backend: BarcodeScannerBackend = backend

  async def scan(self) -> Barcode:
    """Scan a barcode and return its value."""
    return await self.backend.scan_barcode()
