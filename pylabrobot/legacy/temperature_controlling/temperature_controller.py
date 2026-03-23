from typing import Optional

from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControllerBackend as _NewTCBackend,
)
from pylabrobot.legacy.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .backend import TemperatureControllerBackend


class _TemperatureControlAdapter(_NewTCBackend):
  def __init__(self, legacy: TemperatureControllerBackend):
    self._legacy = legacy
  async def setup(self): pass
  async def stop(self): pass
  @property
  def supports_active_cooling(self) -> bool:
    return self._legacy.supports_active_cooling
  async def set_temperature(self, temperature: float):
    await self._legacy.set_temperature(temperature)
  async def get_current_temperature(self) -> float:
    return await self._legacy.get_current_temperature()
  async def deactivate(self):
    await self._legacy.deactivate()


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
    self._tc_cap = TemperatureControlCapability(backend=_TemperatureControlAdapter(backend))

  @property
  def target_temperature(self) -> Optional[float]:
    return self._tc_cap.target_temperature

  @target_temperature.setter
  def target_temperature(self, value: Optional[float]):
    self._tc_cap.target_temperature = value

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._tc_cap._on_setup()

  async def set_temperature(self, temperature: float, passive: bool = False):
    return await self._tc_cap.set_temperature(temperature, passive=passive)

  async def get_temperature(self) -> float:
    return await self._tc_cap.get_temperature()

  async def wait_for_temperature(self, timeout: float = 300.0, tolerance: float = 0.5) -> None:
    return await self._tc_cap.wait_for_temperature(timeout=timeout, tolerance=tolerance)

  async def deactivate(self):
    return await self._tc_cap.deactivate()

  async def stop(self):
    await self._tc_cap._on_stop()
    await super().stop()

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }
