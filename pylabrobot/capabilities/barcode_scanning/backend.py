from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources.barcode import Barcode


class BarcodeScannerError(Exception):
  """Error raised by a barcode scanner backend."""


class BarcodeScannerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for barcode scanning devices."""

  @abstractmethod
  async def scan_barcode(self, read_time: Optional[float] = None) -> Barcode:
    """Scan a barcode and return its value.

    Args:
      read_time: Optional read-window in seconds. ``None`` means use whatever
        default the underlying device is currently configured with. Backends
        for devices that don't expose a configurable window may ignore it.
    """
