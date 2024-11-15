from pylabrobot.centrifuge.backend import CentrifugeBackend
from pylabrobot.machines.machine import Machine


class Centrifuge(Machine):
  """The front end for centrifuges.
  Centrifuges are devices that can spin samples at high speeds."""

  def __init__(self, backend: CentrifugeBackend) -> None:
    super().__init__(backend=backend)
    self.backend: CentrifugeBackend = backend  # fix type

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
    await self.backend.rotate_distance(distance=distance)

  async def start_spin_cycle(self, g: float, duration: float, acceleration: float) -> None:
    await self.backend.start_spin_cycle(
      g=g,
      duration=duration,
      acceleration=acceleration,
    )
