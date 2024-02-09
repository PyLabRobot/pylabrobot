from abc import ABCMeta, abstractmethod
from pylabrobot.machine import MachineBackend


class ScaleBackend(MachineBackend, metaclass=ABCMeta):
  """ Backend for a scale """

  @abstractmethod
  async def tare(self):
    ...

  @abstractmethod
  async def get_weight(self) -> float:
    """ Get the weight in grams """

  @abstractmethod
  async def zero(self):
    ...
