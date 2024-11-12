from pylabrobot.machines.machine import Machine
from pylabrobot.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder


class Centrifuge(Machine):
  """The front end for centrifuges.
  Centrifuges are devices that can spin samples at high speeds."""

  def __init__(self, backend: CentrifugeBackend) -> None:
    super().__init__(backend=backend)
    self.backend: CentrifugeBackend = backend  # fix type
    self._door_open = False

  async def open_door(self) -> None:
    await self.backend.open_door()
    self._door_open = True

  async def close_door(self) -> None:
    await self.backend.close_door()
    self._door_open = False

  @property
  def door_open(self) -> bool:
    return self._door_open

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


class CentrifugeDoorError(Exception):
  pass


class Loader(Machine, ResourceHolder):
  """The front end for centrifuge loaders.
  Centrifuge loaders are devices that can load and unload samples from centrifuges."""

  def __init__(self, backend: LoaderBackend, centrifuge: Centrifuge, stage_location: Coordinate) -> None:
    super().__init__(backend=backend)
    self.backend: LoaderBackend = backend  # fix type
    self.centrifuge: Centrifuge = centrifuge
    self.stage_location = stage_location

  def get_default_child_location(self, resource):
    return super().get_default_child_location(resource) + self.stage_location

  async def load(self) -> None:
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to load a plate.")
    await self.backend.load()

  async def unload(self) -> None:
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to unload a plate.")
    await self.backend.unload()
