"""Legacy. Use pylabrobot.hamilton.heater_shaker instead."""

import warnings
from typing import Dict, Literal, Optional

from pylabrobot.hamilton.heater_shaker import backend as hhs_backend
from pylabrobot.hamilton.heater_shaker import box
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend

HamiltonHeaterShakerInterface = box.HamiltonHeaterShakerInterface
HamiltonHeaterShakerBox = box.HamiltonHeaterShakerBox


class HamiltonHeaterShakerBackend(HeaterShakerBackend):
  """Legacy. Use pylabrobot.hamilton.heater_shaker.HamiltonHeaterShakerBackend instead."""

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    self._new = hhs_backend.HamiltonHeaterShakerBackend(index=index, interface=interface)

  @property
  def supports_active_cooling(self) -> bool:
    return self._new.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._new.supports_locking

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return self._new.serialize()

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    await self._new.start_shaking(
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
    await self._new.stop_shaking()

  async def get_is_shaking(self) -> bool:
    return await self._new.get_is_shaking()

  async def lock_plate(self):
    await self._new.lock_plate()

  async def unlock_plate(self):
    await self._new.unlock_plate()

  async def set_temperature(self, temperature: float):
    await self._new.set_temperature(temperature=temperature)

  async def get_current_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def _get_current_temperature(self) -> Dict[str, float]:
    return await self._new._get_current_temperature()

  async def get_edge_temperature(self) -> float:
    return await self._new.get_edge_temperature()

  async def deactivate(self):
    await self._new.deactivate()
