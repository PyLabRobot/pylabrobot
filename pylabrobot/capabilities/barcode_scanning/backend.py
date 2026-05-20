from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources.barcode import Barcode


class BarcodeScannerError(Exception):
  """Error raised by a barcode scanner backend."""


class BarcodeScannerBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for barcode scanning devices."""

  @abstractmethod
  async def scan_barcode(self, read_time: Optional[float] = None) -> Optional[Barcode]:
    """Scan a barcode and return its value, or ``None`` if no barcode is
    decoded within the read window.

    Args:
      read_time: Optional read-window in seconds. ``None`` means use whatever
        default the underlying device is currently configured with. Backends
        for devices that don't expose a configurable window may ignore it.

    Returns:
      The decoded :class:`Barcode`, or ``None`` if the read window elapsed
      with no successful decode. Backends still raise
      :class:`BarcodeScannerError` for hardware faults (reader off, comms
      failure) — ``None`` is reserved for the "nothing seen" case.
    """
