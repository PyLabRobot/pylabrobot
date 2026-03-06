from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class SealerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a sealer machine"""

  @abstractmethod
  async def seal(self, temperature: int, duration: float): ...

  @abstractmethod
  async def open(self): ...

  @abstractmethod
  async def close(self): ...

  @abstractmethod
  async def set_temperature(self, temperature: float):
    """Set the temperature of the sealer in degrees Celsius."""

  @abstractmethod
  async def get_temperature(self) -> float:
    """Get the current temperature of the sealer in degrees Celsius."""
