from typing import Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.centrifuge.backend import CentrifugeBackend

class Centrifuge(Machine):
  """ The front end for centrifuges.
  Centrifuges are devices that can spin samples at high speeds."""

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

  async def rotate_distance(self, distance) -> None:
    await self.backend.rotate_distance(distance = distance)

  async def start_spin_cycle(self, g: float, duration: float, acceleration: float) -> None:
    await self.backend.start_spin_cycle(
      g=g,
      duration=duration,
      acceleration=acceleration,
    )
