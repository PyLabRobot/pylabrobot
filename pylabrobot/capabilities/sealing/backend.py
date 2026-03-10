from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class SealerBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for sealing devices."""

  @abstractmethod
  async def seal(self, temperature: int, duration: float):
    """Perform a seal operation at the given temperature and duration."""

  @abstractmethod
  async def open(self):
    """Open the sealer shuttle."""

  @abstractmethod
  async def close(self):
    """Close the sealer shuttle."""
