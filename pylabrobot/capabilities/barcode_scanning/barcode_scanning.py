from typing import Optional

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.resources.barcode import Barcode

from .backend import BarcodeScannerBackend


class BarcodeScanner(Capability):
  """Barcode scanning capability.

  See :doc:`/user_guide/capabilities/barcode-scanning` for a walkthrough.
  """

  def __init__(self, backend: BarcodeScannerBackend):
    super().__init__(backend=backend)
    self.backend: BarcodeScannerBackend = backend

  @need_capability_ready
  async def scan(self, read_time: Optional[float] = None) -> Barcode:
    """Scan a barcode and return its value.

    Args:
      read_time: Optional read-window in seconds for this scan. If omitted,
        the backend uses the device's current default. Backends for scanners
        without a configurable window may ignore this argument.
    """
    return await self.backend.scan_barcode(read_time=read_time)

  async def _on_stop(self):
    await super()._on_stop()
