from pylabrobot.capabilities.barcode_scanning import BarcodeScanningCapability
from pylabrobot.device import Device

from .keyence_backend import (
  KeyenceBarcodeScannerBarcodeScanningBackend,
  KeyenceBarcodeScannerDriver,
)


class KeyenceBarcodeScanner(Device):
  """Keyence BL-series barcode scanner (BL-600HA, BL-1300)."""

  def __init__(self, port: str):
    driver = KeyenceBarcodeScannerDriver(port=port)
    super().__init__(driver=driver)
    self._driver: KeyenceBarcodeScannerDriver = driver
    self.barcode_scanning = BarcodeScanningCapability(
      backend=KeyenceBarcodeScannerBarcodeScanningBackend(driver)
    )
    self._capabilities = [self.barcode_scanning]
