from pylabrobot.capabilities.capability import Capability
from pylabrobot.resources.barcode import Barcode

from .backend import BarcodeScannerBackend


class BarcodeScanner(Capability):
  """Barcode scanning capability.

  See :doc:`/user_guide/capabilities/barcode-scanning` for a walkthrough.
  """

  def __init__(self, backend: BarcodeScannerBackend):
    super().__init__(backend=backend)
    self.backend: BarcodeScannerBackend = backend

  async def scan(self) -> Barcode:
    """Scan a barcode and return its value."""
    return await self.backend.scan_barcode()

  async def _on_stop(self):
    await super()._on_stop()
