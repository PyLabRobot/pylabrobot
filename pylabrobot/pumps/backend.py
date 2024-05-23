from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machines.backends import MachineBackend


class PumpBackend(MachineBackend, metaclass=ABCMeta):
  """ Abstract base class for pump backends. """

  def run_revolutions(self, num_revolutions: float):
    """ Run for a given number of revolutions.

    Args:
      num_revolutions: number of revolutions to run.
    """

  def run_continuously(self, speed: float):
    """ Run continuously at a given speed.

    If speed is 0, the pump will be halted.

    Args:
      speed: speed in rpm/pump-specific units.
    """

  def halt(self):
    """ Halt the pump. """

  async def stop(self):
    """ Close the connection to the pump. """


class PumpArrayBackend(MachineBackend, metaclass=ABCMeta):
  """
  Abstract base class for pump array backends.

  For more information on some methods and arguments, see the documentation for the
  :class:`~PumpArray` class.
  """

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """ The number of channels that the pump array has. """

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    """Run the specified channels at the speed selected.
    If speed is 0, the pump will be halted.

    Args:
      num_revolutions: number of revolutions to run pumps.
      use_channels: pump array channels to run
    """

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    """Run for a given number of revolutions.
    Args:
      speed: rate at which to run pump.
      use_channels: pump array channels to run
    """

  async def halt(self):
    """ Halt the entire pump array. """

  async def stop(self):
    """ Close the connection to the pump array. """
