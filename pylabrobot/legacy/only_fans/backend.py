from abc import ABCMeta, abstractmethod

from pylabrobot.legacy.machines.backend import MachineBackend


class FanBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract base class for fan backends."""

  @abstractmethod
  async def turn_on(self, intensity: int) -> None:
    """Run the fan at intensity: integer percent between 0 and 100"""

  @abstractmethod
  async def turn_off(self) -> None:
    """Stop the fan, but don't close the connection."""
