import asyncio
from typing import Any, Optional

from pylabrobot.events import evented_operation, resource_reference
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import ShakerBackend


def _shaker_controller_event_context(self: "Shaker") -> dict[str, Any]:
  context: dict[str, Any] = {"device": resource_reference(self)}
  if self.resource is not None:
    context["resources"] = [resource_reference(self.resource)]
  return context


def _shake_event_context(
  self: "Shaker",
  speed: float,
  duration: Optional[float] = None,
  **backend_kwargs: Any,
) -> dict[str, Any]:
  """Describe a shaker operation and its directly loaded resource, when present."""

  context = _shaker_controller_event_context(self)
  context["speed_rpm"] = float(speed)
  if duration is not None:
    context["duration"] = float(duration)
  return context


def _stop_shaking_event_context(self: "Shaker", **backend_kwargs: Any) -> dict[str, Any]:
  return _shaker_controller_event_context(self)


class Shaker(ResourceHolder, Machine):
  """A shaker machine"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ShakerBackend,
    child_location: Coordinate,
    category: str = "shaker",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      child_location=child_location,
    )
    Machine.__init__(self, backend=backend)
    self.backend: ShakerBackend = backend  # fix type

  @evented_operation("shaker.shake", _shake_event_context)
  async def shake(self, speed: float, duration: Optional[float] = None, **backend_kwargs):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
      duration: Duration of shaking in seconds. If None, shake indefinitely (and return immediately).
    """
    if self.backend.supports_locking:
      await self.backend.lock_plate()
    await self.backend.start_shaking(speed=speed, **backend_kwargs)

    if duration is None:
      return

    await asyncio.sleep(duration)
    await self.backend.stop_shaking()
    if self.backend.supports_locking:
      await self.backend.unlock_plate()

  @evented_operation("shaker.stop_shaking", _stop_shaking_event_context)
  async def stop_shaking(self, **backend_kwargs):
    await self.backend.stop_shaking(**backend_kwargs)

  async def lock_plate(self, **backend_kwargs):
    await self.backend.lock_plate(**backend_kwargs)

  async def unlock_plate(self, **backend_kwargs):
    await self.backend.unlock_plate(**backend_kwargs)

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }
