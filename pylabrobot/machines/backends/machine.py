from abc import ABC, abstractmethod


class MachineBackend(ABC):
  """ Abstract class for machine backends. """

  @abstractmethod
  async def setup(self):
    pass

  @abstractmethod
  async def stop(self):
    pass

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__}
