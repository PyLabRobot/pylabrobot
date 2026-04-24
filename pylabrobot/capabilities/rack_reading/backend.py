from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend

from .standard import LayoutInfo, RackReaderState, RackScanResult


class RackReaderBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for rack readers that decode position-indexed rack contents."""

  @abstractmethod
  async def get_state(self) -> RackReaderState:
    """Return the current rack reader state."""

  @abstractmethod
  async def trigger_rack_scan(self) -> None:
    """Initiate a rack-wide scan."""

  @abstractmethod
  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    """Perform a rack-barcode-only scan and return the rack identifier.

    Backends whose hardware exposes a one-shot rack-id read may ignore
    ``timeout`` and ``poll_interval``; backends that need a trigger/poll cycle
    should respect them.
    """

  @abstractmethod
  async def get_scan_result(self) -> RackScanResult:
    """Return the most recent rack scan result."""

  @abstractmethod
  async def get_rack_id(self) -> str:
    """Return the rack identifier reported by the scanner."""

  @abstractmethod
  async def get_layouts(self) -> list[LayoutInfo]:
    """Return supported layouts."""

  @abstractmethod
  async def get_current_layout(self) -> str:
    """Return the active layout."""

  @abstractmethod
  async def set_current_layout(self, layout: str) -> None:
    """Set the active layout."""
