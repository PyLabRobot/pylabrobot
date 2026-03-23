from abc import ABCMeta, abstractmethod

from pylabrobot.device import DeviceBackend


class CentrifugeBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for centrifuge devices."""

  @abstractmethod
  async def open_door(self) -> None:
    """Open the centrifuge door."""

  @abstractmethod
  async def close_door(self) -> None:
    """Close the centrifuge door."""

  @abstractmethod
  async def lock_door(self) -> None:
    """Lock the centrifuge door."""

  @abstractmethod
  async def unlock_door(self) -> None:
    """Unlock the centrifuge door."""

  @abstractmethod
  async def go_to_bucket1(self) -> None:
    """Rotate to bucket 1 position."""

  @abstractmethod
  async def go_to_bucket2(self) -> None:
    """Rotate to bucket 2 position."""

  @abstractmethod
  async def lock_bucket(self) -> None:
    """Lock the bucket."""

  @abstractmethod
  async def unlock_bucket(self) -> None:
    """Unlock the bucket."""

  @abstractmethod
  async def spin(self, g: float, duration: float, **kwargs) -> None:
    """Start a spin cycle.

    Args:
      g: The g-force to spin at.
      duration: The duration of the spin in seconds (time at speed).
    """
