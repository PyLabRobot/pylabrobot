"""Legacy. Use pylabrobot.hamilton.heater_shaker instead."""

import warnings
from typing import Dict, Literal, Optional

from pylabrobot.hamilton.heater_shaker import backend as hhs_backend
from pylabrobot.hamilton.heater_shaker import box
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend

HamiltonHeaterShakerInterface = box.HamiltonHeaterShakerInterface
HamiltonHeaterShakerBox = box.HamiltonHeaterShakerBox


class HamiltonHeaterShakerBackend(HeaterShakerBackend):
  """Legacy. Use pylabrobot.hamilton.heater_shaker instead."""

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    self._driver = hhs_backend.HamiltonHeaterShakerDriver(index=index, interface=interface)
    self._shaker = hhs_backend.HamiltonHeaterShakerShakerBackend(self._driver)
    self._temp = hhs_backend.HamiltonHeaterShakerTemperatureBackend(self._driver)

  @property
  def supports_active_cooling(self) -> bool:
    return self._temp.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._shaker.supports_locking

  async def setup(self):
    await self._driver.setup()
    await self._shaker._on_setup()
    await self._temp._on_setup()

  async def stop(self):
    await self._temp._on_stop()
    await self._shaker._on_stop()
    await self._driver.stop()

  def serialize(self) -> dict:
    return self._driver.serialize()

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    await self._shaker.start_shaking(
      speed=speed, direction=direction, acceleration=acceleration, timeout=timeout
    )

  async def shake(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    warnings.warn(
      "HamiltonHeaterShakerBackend.shake() is deprecated. Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.start_shaking(
      speed=speed, direction=direction, acceleration=acceleration, timeout=timeout
    )

  async def stop_shaking(self):
    await self._shaker.stop_shaking()

  async def get_is_shaking(self) -> bool:
    return await self._shaker.request_is_shaking()

  async def lock_plate(self):
    await self._shaker.lock_plate()

  async def unlock_plate(self):
    await self._shaker.unlock_plate()

  async def set_temperature(self, temperature: float):
    await self._temp.set_temperature(temperature=temperature)

  async def get_current_temperature(self) -> float:
    return await self._temp.request_current_temperature()

  async def _get_current_temperature(self) -> Dict[str, float]:
    return await self._temp._request_current_temperature()

  async def get_edge_temperature(self) -> float:
    return await self._temp.request_edge_temperature()

  async def deactivate(self):
    await self._temp.deactivate()
