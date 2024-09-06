import sys
from typing import List, Optional, cast

from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.centrifuge.backend import CentrifugeBackend

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal

class Centrifuge(Machine):
  """ The front end for centrifuges. Centrifuges are devices that can spin samples at high speeds."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: CentrifugeBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ) -> None:
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, backend=backend,
                      category=category, model=model)
    self.backend: CentrifugeBackend = backend # fix type

  async def stop(self) -> None:
    await self.backend.stop()

  async def open_door(self) -> None:
    await self.backend.open_door()

  async def close_door(self) -> None:
    await self.backend.close_door()

  async def lock_door(self) -> None:
    await self.backend.lock_door()

  async def unlock_door(self) -> None:
    await self.backend.unlock_door()

  async def unlock_bucket(self) -> None:
    await self.backend.unlock_bucket()

  async def lock_bucket(self) -> None:
    await self.backend.lock_bucket()

  async def go_to_bucket1(self) -> None:
    await self.backend.go_to_bucket1()

  async def go_to_bucket2(self) -> None:
    await self.backend.go_to_bucket2()

  async def start_spin_cycle(
    self,
    plates: Optional[Plate] = None,
    g: Optional[float] = None,
    time_seconds: Optional[float] = None,
    acceleration: Optional[float] = None,
    deceleration: Optional[float] = None,
  ) -> None:
    await self.backend.start_spin_cycle(
      plates=plates,
      g=g,
      time_seconds=time_seconds,
      acceleration=acceleration,
      deceleration=deceleration,
    )
