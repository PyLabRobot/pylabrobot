from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class ScaleBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract backend for scales."""

  @abstractmethod
  async def zero(self):
    """Zero the scale."""

  @abstractmethod
  async def tare(self):
    """Tare the scale."""

  @abstractmethod
  async def read_weight(self) -> float:
    """Read the weight in grams."""
