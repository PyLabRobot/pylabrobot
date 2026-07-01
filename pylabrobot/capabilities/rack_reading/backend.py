from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources.tube_rack import TubeRack

from .standard import RackScanResult


class RackReaderBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for rack readers that decode position-indexed rack contents."""

  @abstractmethod
  async def scan_rack(self, rack: TubeRack, timeout: float, poll_interval: float) -> RackScanResult:
    """Scan ``rack`` and return its decoded contents."""

  @abstractmethod
  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    """Read the rack barcode only and return the rack identifier."""
