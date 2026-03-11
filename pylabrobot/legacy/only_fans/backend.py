from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class FanBackend(MachineBackend, metaclass=ABCMeta):
  """Legacy. Use pylabrobot.capabilities.fan_control.FanBackend instead."""

  @abstractmethod
  async def setup(self) -> None:
    """Set up the fan."""

  @abstractmethod
  async def turn_on(self, intensity: int) -> None:
    """Run the fan at intensity: integer percent between 0 and 100."""

  @abstractmethod
  async def turn_off(self) -> None:
    """Stop the fan."""

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the fan."""
