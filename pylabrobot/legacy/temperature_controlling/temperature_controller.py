from typing import Optional

from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability

from .backend import TemperatureControllerBackend


class TemperatureController(ResourceHolder, Machine):
  """Legacy. Use pylabrobot.inheco.InhecoCPAC (or vendor-specific class) instead."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: TemperatureControllerBackend,
    child_location: Coordinate,
    category: str = "temperature_controller",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: TemperatureControllerBackend = backend
    self._cap = TemperatureControlCapability(backend=backend)

  @property
  def target_temperature(self) -> Optional[float]:
    return self._cap.target_temperature

  @target_temperature.setter
  def target_temperature(self, value: Optional[float]):
    self._cap.target_temperature = value

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._cap._on_setup()

  async def set_temperature(self, temperature: float, passive: bool = False):
    return await self._cap.set_temperature(temperature, passive=passive)

  async def get_temperature(self) -> float:
    return await self._cap.get_temperature()

  async def wait_for_temperature(self, timeout: float = 300.0, tolerance: float = 0.5) -> None:
    return await self._cap.wait_for_temperature(timeout=timeout, tolerance=tolerance)

  async def deactivate(self):
    return await self._cap.deactivate()

  async def stop(self):
    await self._cap._on_stop()
    await super().stop()

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }
