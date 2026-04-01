from abc import ABCMeta, abstractmethod
from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Plate


class SyringeDispensingBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for syringe pump dispensing devices."""

  @abstractmethod
  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid using the syringe pump.

    Args:
      plate: Target plate.
      volumes: Mapping of 1-indexed column number to volume in uL.
        Example: {1: 100, 2: 100, 3: 200, 7: 50}
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def prime(
    self,
    plate: Plate,
    volume: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime the syringe pump system.

    Args:
      plate: Target plate.
      volume: Prime volume in uL.
      backend_params: Backend-specific parameters.
    """
