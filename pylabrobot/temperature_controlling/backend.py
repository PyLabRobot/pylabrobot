from abc import ABCMeta, abstractmethod
from pylabrobot.machine import MachineBackend


class TemperatureControllerBackend(MachineBackend, metaclass=ABCMeta):
  @abstractmethod
  async def set_temperature(self, temperature: float):
    """ Set the temperature of the temperature controller in Celsius. """

  @abstractmethod
  async def get_current_temperature(self) -> float:
    """ Get the current temperature of the temperature controller in Celsius """

  @abstractmethod
  async def deactivate(self):
    """ Deactivate the temperature controller. """
