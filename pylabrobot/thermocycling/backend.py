from abc import ABCMeta, abstractmethod
from pylabrobot.machines.backends import MachineBackend


class ThermocyclerBackend(MachineBackend, metaclass=ABCMeta):

  @abstractmethod
  async def open_lid(self):
    """ Open lid of the thermocycler. """

  @abstractmethod
  async def close_lid(self):
    """ Close lid of the thermocycler. """

  @abstractmethod
  async def get_lid_status(self):
    """ Get status of lid on the thermocycler (open or closed). """

  @abstractmethod
  async def set_temperature(self, temperature: float):
    """ Set the temperature of the thermocycler in Celsius. """

  @abstractmethod
  async def set_lid_temperature(self, temperature: float):
    """ Set the lid temperature of the thermocycler in Celsius. """

  @abstractmethod
  async def set_block_temperature(self, temperature: float):
    """ Set the block temperature of the thermocycler in Celsius. """

  @abstractmethod
  async def get_temperature(self) -> float:
    """ Get the current temperature of the thermocycler in Celsius """

  @abstractmethod
  async def get_lid_temperature(self) -> float:
    """ Get the current lid temperature of the thermocycler in Celsius """

  @abstractmethod
  async def get_block_temperature(self) -> float:
    """ Get the current block temperature of the thermocycler in Celsius """

  @abstractmethod
  async def deactivate_lid(self):
    """ Deactivate the lid of the thermocycler (turns off heat)."""

  @abstractmethod
  async def deactivate_block(self):
    """ Deactivate the block of the thermocycler (turns off heat). """

  @abstractmethod
  async def deactivate(self):
    """ Deactivate the thermocycler (turn of heat)"""

  @abstractmethod
  async def run_profule(self, profile: list, block_max_volume: float):
    """ Run a thermocycler profile """
