from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.machines.backends import MachineBackend

class CentrifugeBackend(MachineBackend, metaclass=ABCMeta):
  """ An abstract class for a centrifuge"""
  @abstractmethod
  async def setup(self) -> None:
    """ Set up the centrifuge. This should be called before any other methods. """

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the centrifuge. """

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
  async def start_spin_cycle(
  self,
  g: Optional[float] = None,
  time_seconds: Optional[float] = None,
  acceleration: Optional[float] = None,
 ) -> None:
    """ Takes user settings and starts spinning buckets. Also known as start spin cycle. """
