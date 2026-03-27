from abc import ABCMeta, abstractmethod

from pylabrobot.capabilities.capability import CapabilityBackend


class PumpBackend(CapabilityBackend, metaclass=ABCMeta):
  """Abstract backend for a single pump."""

  @abstractmethod
  async def run_revolutions(self, num_revolutions: float):
    """Run for a given number of revolutions."""

  @abstractmethod
  async def run_continuously(self, speed: float):
    """Run continuously at a given speed. If speed is 0, halt."""

  @abstractmethod
  async def halt(self):
    """Halt the pump."""
