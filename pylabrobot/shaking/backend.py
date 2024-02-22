from abc import ABCMeta, abstractmethod
from pylabrobot.machine import MachineBackend


class ShakerBackend(MachineBackend, metaclass=ABCMeta):
  """ Backend for a shaker machine """

  @abstractmethod
  async def shake(self, speed: float):
    """ Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """

  @abstractmethod
  async def stop_shaking(self):
    """ Stop shaking """
