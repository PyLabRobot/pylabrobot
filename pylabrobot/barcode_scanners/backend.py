from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend

class BarcodeScannerError(Exception):
    """Error raised by a barcode scanner backend."""

class BarcodeScannerBackend(MachineBackend, metaclass=ABCMeta):
    def __init__(self):
        super().__init__()

    @abstractmethod
    async def scan_barcode(self) -> str:
        """Scan a barcode and return its value as a string."""
        pass
