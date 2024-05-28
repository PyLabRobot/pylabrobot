from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backends import MachineBackend


class FanBackend(MachineBackend, metaclass=ABCMeta):
  """ Abstract base class for fan backends. """

  @abstractmethod
  async def setup(self) -> None:
    """ Set up the fan. This should be called before any other methods. """

  @abstractmethod
  async def turn_on_fan(self, speed: int) -> None:
    """ Turn the fan on at a specified speed, between 0-100 as an integer. Allows for run time
    duration to be set (in seconds)  """

  @abstractmethod
  async def stop_fan(self) -> None:
    """ Stop the fan, but doesn't close the connection. """

  @abstractmethod
  async def stop(self) -> None:
    """ Close all connections to the fan and make sure setup() can be called again. """
