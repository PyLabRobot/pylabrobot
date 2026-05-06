"""Shared contracts for Micronic rack-reader drivers."""

from __future__ import annotations

from abc import abstractmethod

from pylabrobot.capabilities.rack_reading import (
  LayoutInfo,
  RackReaderState,
  RackScanResult,
)
from pylabrobot.device import Driver


class MicronicError(Exception):
  """Raised when Micronic driver operations fail."""


class MicronicRackReaderDriver(Driver):
  """Driver contract used by the Micronic rack-reading backend."""

  @abstractmethod
  async def get_rack_reader_state(self) -> RackReaderState:
    """Return the current rack-reader state."""

  @abstractmethod
  async def trigger_rack_scan(self) -> None:
    """Initiate a rack-wide scan."""

  @abstractmethod
  async def scan_rack_id(self, timeout: float, poll_interval: float) -> str:
    """Perform a rack-barcode-only scan and return the rack identifier."""

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
