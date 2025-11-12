from __future__ import annotations

from abc import ABCMeta, abstractmethod

from pylabrobot.machines.backend import MachineBackend


class CentrifugeBackend(MachineBackend, metaclass=ABCMeta):
  """An abstract class for a centrifuge"""

  @abstractmethod
  async def open_door(self) -> None:
    pass

  @abstractmethod
  async def close_door(self) -> None:
    pass

  @abstractmethod
  async def lock_door(self) -> None:
    pass

  @abstractmethod
  async def unlock_door(self) -> None:
    pass

  @abstractmethod
  async def go_to_bucket1(self) -> None:
    pass

  @abstractmethod
  async def go_to_bucket2(self) -> None:
    pass

  @abstractmethod
  async def lock_bucket(self) -> None:
    pass

  @abstractmethod
  async def unlock_bucket(self) -> None:
    pass

  @abstractmethod
  async def spin(self, g: float, duration: float, acceleration: float) -> None:
    pass


class LoaderBackend(MachineBackend, metaclass=ABCMeta):
  """An abstract class for a centrifuge loader"""

  @abstractmethod
  async def load(self) -> None:
    pass

  @abstractmethod
  async def unload(self) -> None:
    pass
