from pylabrobot.barcode_scanners.backend import BarcodeScannerBackend
from pylabrobot.machines.machine import Machine


class BarcodeScanner(Machine):
  """Frontend for barcode scanners."""

  def __init__(self, backend: BarcodeScannerBackend):
    super().__init__(backend=backend)
    self.backend: BarcodeScannerBackend = backend

  async def scan(self) -> str:
    """Scan a barcode and return its value."""
    return await self.backend.scan_barcode()
