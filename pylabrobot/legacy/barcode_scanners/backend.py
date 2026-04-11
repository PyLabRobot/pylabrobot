"""Legacy. Use pylabrobot.capabilities.barcode_scanning.backend instead."""

from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources.barcode import Barcode


class BarcodeScannerError(Exception):
  """Error raised by a barcode scanner backend."""


class BarcodeScannerBackend(MachineBackend, metaclass=ABCMeta):
  def __init__(self):
    super().__init__()

  @abstractmethod
  async def scan_barcode(self) -> Barcode:
    """Scan a barcode and return its value."""
