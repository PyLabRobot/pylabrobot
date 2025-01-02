from typing import Optional

from pylabrobot.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.centrifuge.standard import (
  BucketHasPlateError,
  BucketNoPlateError,
  CentrifugeDoorError,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.machines.machine import Machine
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource_holder import ResourceHolder


class Centrifuge(Machine):
  """The front end for centrifuges."""

  def __init__(self, backend: CentrifugeBackend, loading_tray_location: Coordinate) -> None:
    super().__init__(backend=backend)
    self.backend: CentrifugeBackend = backend  # fix type
    self._door_open = False
    self._at_bucket: Optional[ResourceHolder] = None

    self.bucket1 = ResourceHolder(
      name="centrifuge_bucket1",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    self.bucket2 = ResourceHolder(
      name="cengrifuge_bucket2",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )

    self.bucket1.location = loading_tray_location
    self.bucket2.location = loading_tray_location

  async def open_door(self, **kwargs) -> None:
    await self.backend.open_door(**kwargs)
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
    self._at_bucket = self.bucket1

  async def go_to_bucket2(self) -> None:
    await self.backend.go_to_bucket2()
    self._at_bucket = self.bucket2

  async def rotate_distance(self, distance) -> None:
    await self.backend.rotate_distance(distance=distance)
    self._at_bucket = None

  async def start_spin_cycle(self, g: float, duration: float, acceleration: float) -> None:
    await self.backend.start_spin_cycle(
      g=g,
      duration=duration,
      acceleration=acceleration,
    )
    self._at_bucket = None

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    """None if not at a bucket or unknown, otherwise the resource representing the bucket."""
    return self._at_bucket


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
    self.centrifuge = centrifuge

  async def load(self) -> None:
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to load a plate.")

    if self.centrifuge.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to load a plate, but current position is unknown or not at "
        "a bucket. Use centrifuge.go_to_bucket{1,2}() to move to a bucket."
      )

    if self.resource is None:
      raise LoaderNoPlateError("Loader must have a plate to load.")

    if self.centrifuge.at_bucket.resource is not None:
      raise BucketHasPlateError("Bucket must be empty to load a plate.")

    await self.backend.load()

    self.centrifuge.at_bucket.assign_child_resource(self.resource, location=Coordinate.zero())

  async def unload(self) -> None:  # DOOR arg?
    if not self.centrifuge.door_open:
      raise CentrifugeDoorError("Centrifuge door must be open to unload a plate.")

    if self.centrifuge.at_bucket is None:
      raise NotAtBucketError(
        "Centrifuge must be at a bucket to unload a plate, but current position is unknown or not "
        "at a bucket. Use centrifuge.go_to_bucket{1,2}() to move to a bucket."
      )

    if self.centrifuge.at_bucket.resource is None:
      raise BucketNoPlateError("Bucket must have a plate to unload.")

    await self.backend.unload()

    self.assign_child_resource(self.centrifuge.at_bucket.resource)

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
