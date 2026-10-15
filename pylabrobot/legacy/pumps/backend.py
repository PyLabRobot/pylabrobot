from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.legacy.machines.backend import MachineBackend


class PumpBackend(MachineBackend, metaclass=ABCMeta):
  """Abstract base class for pump backends."""

  @abstractmethod
  async def run_revolutions(self, num_revolutions: float) -> None:
    """Run for a given number of revolutions.

    Args:
      num_revolutions: number of revolutions to run.
    """
    pass

  @abstractmethod
  async def run_continuously(self, speed: float) -> None:
    """Run continuously at a given speed.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm/pump-specific units.
    """
    pass

  @abstractmethod
  async def halt(self) -> None:
    """Halt the pump."""
    pass


class PumpArrayBackend(MachineBackend, metaclass=ABCMeta):
  """
  Abstract base class for pump array backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~PumpArray` class.
  """

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """The number of channels that the pump array has."""

  @abstractmethod
  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]) -> None:
    """Run the selected channels for the requested revolutions."""

  @abstractmethod
  async def run_continuously(self, speed: List[float], use_channels: List[int]) -> None:
    """Run the selected channels at the requested speeds."""

  @abstractmethod
  async def halt(self) -> None:
    """Halt the entire pump array."""
