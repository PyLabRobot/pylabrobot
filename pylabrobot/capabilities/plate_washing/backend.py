from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Plate


class PlateWashingBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for plate washing devices."""

  @abstractmethod
  async def aspirate(
    self,
    plate: Plate,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Aspirate (remove) liquid from all wells.

    Args:
      plate: Target plate.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def dispense(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid into all wells.

    Args:
      plate: Target plate.
      volume: Volume per well in uL.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def wash(
    self,
    plate: Plate,
    cycles: int = 3,
    dispense_volume: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Perform wash cycles (repeated dispense + aspirate).

    Args:
      plate: Target plate.
      cycles: Number of wash cycles.
      dispense_volume: Volume per well per cycle in uL. If None, use device default.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def prime(
    self,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime fluid lines.

    Args:
      backend_params: Backend-specific parameters.
    """
