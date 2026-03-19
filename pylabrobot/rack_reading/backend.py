from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machines.backend import MachineBackend

from .standard import LayoutInfo, RackReaderState, RackScanResult


class RackReaderBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for rack readers that decode position-indexed rack contents."""

  @abstractmethod
  async def setup(self) -> None:
    """Set up the rack reader."""

  @abstractmethod
  async def stop(self) -> None:
    """Stop the rack reader and release resources."""

  @abstractmethod
  async def get_state(self) -> RackReaderState:
    """Return the current rack reader state."""

  @abstractmethod
  async def scan_box(self) -> None:
    """Initiate a rack-wide scan."""

  @abstractmethod
  async def scan_tube(self) -> None:
    """Initiate a single-tube scan."""

  @abstractmethod
  async def get_scan_result(self) -> RackScanResult:
    """Return the most recent rack scan result."""

  @abstractmethod
  async def get_rack_id(self) -> str:
    """Return the rack identifier reported by the scanner."""

  @abstractmethod
  async def get_layouts(self) -> List[LayoutInfo]:
    """Return supported layouts."""

  @abstractmethod
  async def get_current_layout(self) -> str:
    """Return the active layout."""

  @abstractmethod
  async def set_current_layout(self, layout: str) -> None:
    """Set the active layout."""
