from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import CapabilityBackend
from pylabrobot.resources import Plate


class PlateWashingBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for plate washing devices."""

  @abstractmethod
  async def aspirate(self, plate: Plate) -> None:
    """Aspirate (remove) liquid from all wells."""

  @abstractmethod
  async def dispense(self, plate: Plate, volume: float) -> None:
    """Dispense liquid into all wells.

    Args:
      plate: Target plate.
      volume: Volume per well in uL.
    """

  @abstractmethod
  async def wash(
    self,
    plate: Plate,
    cycles: int = 3,
    dispense_volume: Optional[float] = None,
  ) -> None:
    """Perform wash cycles (repeated dispense + aspirate).

    Args:
      plate: Target plate.
      cycles: Number of wash cycles.
      dispense_volume: Volume per well per cycle in uL. If None, use device default.
    """

  @abstractmethod
  async def prime(self) -> None:
    """Prime fluid lines."""
