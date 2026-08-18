import inspect
import warnings
from typing import Any, Mapping, Optional, Tuple, cast

from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend, LoaderBackend
from pylabrobot.legacy.centrifuge.standard import (
  BucketHasPlateError,
  BucketNoPlateError,
  CentrifugeDoorError,
  LoaderNoPlateError,
  NotAtBucketError,
)
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, Resource, ResourceHolder
from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import deserialize


_MISSING_BACKEND_PARAMETER = object()


def _resolved_backend_parameter(
  centrifuge: "Centrifuge", name: str, backend_kwargs: Mapping[str, Any]
) -> Any:
  """Return the explicitly requested or backend-default value for one spin parameter."""
  if name in backend_kwargs:
    return backend_kwargs[name]
  try:
    parameter = inspect.signature(type(centrifuge.backend).spin).parameters.get(name)
  except (TypeError, ValueError):
    return _MISSING_BACKEND_PARAMETER
  if parameter is None or parameter.default is inspect.Parameter.empty:
    return _MISSING_BACKEND_PARAMETER
  return parameter.default


def _centrifuge_spin_event_context(
  self: "Centrifuge", g: float, duration: float, **backend_kwargs: Any
) -> dict:
  bucket_resources = [
    {
      "holder": resource_reference(bucket),
      "resource": resource_reference(bucket.resource),
    }
    for bucket in (self.bucket1, self.bucket2)
    if bucket.resource is not None
  ]
  data = {
    "device": resource_reference(self),
    "resources": [bucket["resource"] for bucket in bucket_resources],
    "bucket_resources": bucket_resources,
    "relative_centrifugal_force": g,
    "duration": duration,
  }
  acceleration = _resolved_backend_parameter(self, "acceleration", backend_kwargs)
  if acceleration is not _MISSING_BACKEND_PARAMETER:
    data["acceleration_fraction"] = acceleration
  deceleration = _resolved_backend_parameter(self, "deceleration", backend_kwargs)
  if deceleration is not _MISSING_BACKEND_PARAMETER:
    data["deceleration_fraction"] = deceleration
  return data


def _loader_load_event_context(self: "Loader") -> dict:
  plate = self.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(self),
    "destination": resource_reference(self.centrifuge.at_bucket),
  }


def _loader_unload_event_context(self: "Loader") -> dict:
  bucket = self.centrifuge.at_bucket
  plate = None if bucket is None else bucket.resource
  return {
    "device": resource_reference(self),
    "resources": [] if plate is None else [resource_reference(plate)],
    "source": resource_reference(bucket),
    "destination": resource_reference(self),
  }


class Centrifuge(Machine, Resource):
  """The front end for centrifuges."""

  def __init__(
    self,
    backend: CentrifugeBackend,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = "centrifuge",
    model: Optional[str] = None,
    buckets: Optional[Tuple[ResourceHolder, ResourceHolder]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
  ) -> None:
    Machine.__init__(self, backend=backend)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
      metadata=metadata,
    )
    self.backend: CentrifugeBackend = backend  # fix type
    self._door_open = False
    self._at_bucket: Optional[ResourceHolder] = None
    if buckets is None:
      self.bucket1 = ResourceHolder(
        name=f"{name}_bucket1",
        size_x=127.76,
        size_y=85.48,
        size_z=0,
        child_location=Coordinate.zero(),
      )
      self.bucket2 = ResourceHolder(
        name=f"{name}_bucket2",
        size_x=127.76,
        size_y=85.48,
        size_z=0,
        child_location=Coordinate.zero(),
      )
    else:
      self.bucket1, self.bucket2 = buckets
    # TODO: figure out good locations for this.
    self.assign_child_resource(self.bucket1, location=Coordinate.zero())
    self.assign_child_resource(self.bucket2, location=Coordinate.zero())

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

  async def go_to_bucket1(self, **backend_kwargs) -> None:
    await self.backend.go_to_bucket1(**backend_kwargs)
    self._at_bucket = self.bucket1

  async def go_to_bucket2(self, **backend_kwargs) -> None:
    await self.backend.go_to_bucket2(**backend_kwargs)
    self._at_bucket = self.bucket2

  async def start_spin_cycle(self, g: float, duration: float) -> None:
    """Deprecated: use `spin` instead."""
    warnings.warn(
      "`start_spin_cycle` is deprecated and will be removed in a future version. Use `spin` instead.",
      DeprecationWarning,
    )
    await self.spin(g=g, duration=duration)

  @evented_operation("centrifuge.spin", _centrifuge_spin_event_context)
  async def spin(self, g: float, duration: float, **backend_kwargs) -> None:
    """Starts a spin cycle.

    Args:
      g: The g-force to spin at.
      duration: The duration of the spin in seconds. Time at speed.
      acceleration: The acceleration as a fraction of maximum acceleration (0-1).
    """
    await self.backend.spin(
      g=g,
      duration=duration,
      **backend_kwargs,
    )
    self._at_bucket = None

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    """None if not at a bucket or unknown, otherwise the resource representing the bucket."""
    return self._at_bucket

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **Resource.serialize(self),
      "buckets": [bucket.serialize() for bucket in [self.bucket1, self.bucket2]],
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False):
    backend = CentrifugeBackend.deserialize(data["backend"])
    buckets = tuple(ResourceHolder.deserialize(bucket) for bucket in data["buckets"])
    assert len(buckets) == 2
    return cls(
      backend=backend,
      name=data["name"],
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      rotation=cast(Optional[Rotation], deserialize(data.get("rotation"))),
      category=data.get("category"),
      model=data.get("model"),
      buckets=buckets,
      metadata=data.get("metadata"),
    )


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
    metadata: Optional[Mapping[str, Any]] = None,
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
      metadata=metadata,
    )
    self.backend: LoaderBackend = backend  # fix type
    self.centrifuge = centrifuge

  @evented_operation("centrifuge_loader.load", _loader_load_event_context)
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

  @evented_operation("centrifuge_loader.unload", _loader_unload_event_context)
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
      "resource": ResourceHolder.serialize(self),
      "machine": Machine.serialize(self),
      "centrifuge": self.centrifuge.serialize(),
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False):
    return cls(
      backend=LoaderBackend.deserialize(data["machine"]["backend"]),
      centrifuge=Centrifuge.deserialize(data["centrifuge"]),
      name=data["resource"]["name"],
      size_x=data["resource"]["size_x"],
      size_y=data["resource"]["size_y"],
      size_z=data["resource"]["size_z"],
      child_location=deserialize(data["resource"]["child_location"]),
      rotation=deserialize(data["resource"].get("rotation")),
      category=data["resource"].get("category"),
      model=data["resource"].get("model"),
      metadata=data["resource"].get("metadata"),
    )
