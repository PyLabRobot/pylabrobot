from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class FanBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for fan devices."""

  @abstractmethod
  async def turn_on(self, intensity: int) -> None:
    """Run the fan at the given intensity (0-100)."""

  @abstractmethod
  async def turn_off(self) -> None:
    """Stop the fan."""
