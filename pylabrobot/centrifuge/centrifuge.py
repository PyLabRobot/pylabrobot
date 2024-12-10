from typing import cast

from pylabrobot.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.machines.machine import Machine
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.serializer import deserialize, serialize


class Centrifuge(Machine):
  """The front end for centrifuges."""

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

  def __init__(
    self,
    backend: LoaderBackend,
    centrifuge: Centrifuge,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    child_location: Coordinate,
    rotation=None,
    category="loader",
    model=None,
  ) -> None:
    Machine.__init__(self, backend=backend)
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      rotation=rotation,
      category=category,
      model=model,
    )
    self.backend: LoaderBackend = backend  # fix type
    self.centrifuge: Centrifuge = centrifuge

  async def load(self) -> None:
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to load a plate.")
    await self.backend.load()
    # TODO: assign plate to centrifuge bucket, at no location

  async def unload(self) -> None:  # DOOR arg?
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to unload a plate.")
    await self.backend.unload()
    # TODO: assign plate from centrifuge bucket to self

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "resource": ResourceHolder.serialize(self),
      "machine": Machine.serialize(self),
      "centrifuge": self.centrifuge.serialize(),
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshall: bool = False):
    data_copy = data.copy()  # copy data because we will be modifying it
    centrifuge_data = data_copy.pop("centrifuge")
    centrifuge = Centrifuge.deserialize(centrifuge_data)
    return cls(
      centrifuge=centrifuge,
      **data_copy,
    )
