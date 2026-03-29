import logging

from pylabrobot.resources.barcode import Barcode

from .backend import BarcodeScannerBackend

logger = logging.getLogger(__name__)


class BarcodeScannerChatterboxBackend(BarcodeScannerBackend):
  """Chatterbox backend for device-free testing."""

  def __init__(self, barcode: str = "CHATTERBOX-001"):
    self.barcode = barcode

  async def scan_barcode(self) -> Barcode:
    logger.info("Scanning barcode.")
    return Barcode(data=self.barcode, symbology="Code 128 (Subset B and C)", position_on_resource="front")
