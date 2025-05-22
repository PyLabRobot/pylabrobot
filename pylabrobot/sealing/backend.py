from abc import ABCMeta, abstractmethod
from pylabrobot.machines.backends import MachineBackend


class SealerBackend(MachineBackend, metaclass=ABCMeta):
  """Backend for a sealer machine"""

  @abstractmethod
  async def seal(self, temperature: int, duration: float):
    ...

  @abstractmethod
  async def open_shuttle(self):
    ...

  @abstractmethod
  async def close_shuttle(self):
    ...
