from abc import ABCMeta, abstractmethod

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.machines.backend import MachineBackend


class FanBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract base class for fan backends."""

  @abstractmethod
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Set up the fan. This should be called before any other methods."""

  @abstractmethod
  async def turn_on(self, intensity: int) -> None:
    """Run the fan at intensity: integer percent between 0 and 100"""

  @abstractmethod
  async def turn_off(self) -> None:
    """Stop the fan, but don't close the connection."""
