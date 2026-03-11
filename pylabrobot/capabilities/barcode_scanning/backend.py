from abc import ABCMeta, abstractmethod

from pylabrobot.device import DeviceBackend
from pylabrobot.resources.barcode import Barcode


class BarcodeScannerError(Exception):
  """Error raised by a barcode scanner backend."""


class BarcodeScannerBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for barcode scanning devices."""

  @abstractmethod
  async def scan_barcode(self) -> Barcode:
    """Scan a barcode and return its value."""
