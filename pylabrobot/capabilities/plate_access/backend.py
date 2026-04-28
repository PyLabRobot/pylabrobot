from __future__ import annotations

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from pylabrobot.capabilities.capability import CapabilityBackend


@dataclass
class PlateAccessState:
  """Machine access state returned by plate-access capable devices."""

  source_access_open: Optional[bool] = None
  source_access_closed: Optional[bool] = None
  destination_access_open: Optional[bool] = None
  destination_access_closed: Optional[bool] = None
  door_open: Optional[bool] = None
  door_closed: Optional[bool] = None
  source_plate_position: Optional[int] = None
  destination_plate_position: Optional[int] = None
  raw: Dict[str, Any] = field(default_factory=dict)

  @property
  def active_access_paths(self) -> tuple[str, ...]:
    """Names of access paths currently known to be open."""
    active: list[str] = []
    if self.source_access_open is True:
      active.append("source")
    if self.destination_access_open is True:
      active.append("destination")
    return tuple(active)


class PlateAccessBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for plate access operations."""

  @abstractmethod
  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    """Lock the machine for exclusive control."""

  @abstractmethod
  async def unlock(self) -> None:
    """Release the machine lock held by this client."""

  @abstractmethod
  async def get_access_state(self) -> PlateAccessState:
    """Poll the current access state."""

  @abstractmethod
  async def open_source_plate(self, timeout: Optional[float] = None) -> None:
    """Present the source-side plate access path."""

  @abstractmethod
  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    """Retract the source-side access path."""

  @abstractmethod
  async def open_destination_plate(self, timeout: Optional[float] = None) -> None:
    """Present the destination-side plate access path."""

  @abstractmethod
  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> None:
    """Retract the destination-side access path."""

  @abstractmethod
  async def close_door(self, timeout: Optional[float] = None) -> None:
    """Close the machine door."""
