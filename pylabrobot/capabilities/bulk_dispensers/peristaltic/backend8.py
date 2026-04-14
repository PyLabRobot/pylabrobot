from abc import ABCMeta, abstractmethod
from typing import Dict, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Plate


class PeristalticDispensingBackend8(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for peristaltic pump dispensing devices."""

  @abstractmethod
  async def dispense(
    self,
    plate: Plate,
    volumes: Dict[int, float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Dispense liquid using the peristaltic pump.

    Args:
      plate: Target plate.
      volumes: Mapping of 1-indexed column number to volume in uL.
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def prime(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Prime peristaltic fluid lines.

    Args:
      plate: Target plate.
      volume: Prime volume in uL (mutually exclusive with duration).
      duration: Prime duration in seconds (mutually exclusive with volume).
      backend_params: Backend-specific parameters.
    """

  @abstractmethod
  async def purge(
    self,
    plate: Plate,
    volume: Optional[float] = None,
    duration: Optional[int] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Purge peristaltic fluid lines.

    Args:
      plate: Target plate.
      volume: Purge volume in uL (mutually exclusive with duration).
      duration: Purge duration in seconds (mutually exclusive with volume).
      backend_params: Backend-specific parameters.
    """
