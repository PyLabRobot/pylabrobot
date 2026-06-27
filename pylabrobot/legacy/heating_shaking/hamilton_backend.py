"""Legacy. Use pylabrobot.hamilton.heater_shaker instead."""

import warnings
from typing import Dict, Literal, Optional

from pylabrobot.hamilton.heater_shaker.backend import HamiltonHeaterShakerBackend as _NewBackend
from pylabrobot.hamilton.usb.driver import HamiltonUSBDriver
from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend


class HamiltonHeaterShakerBackend(HeaterShakerBackend):
  """Legacy. Use pylabrobot.hamilton.heater_shaker instead."""

  def __init__(self, index: int, interface: HamiltonUSBDriver) -> None:
    self._backend = _NewBackend(driver=interface, index=index)

  @property
  def supports_active_cooling(self) -> bool:
    return self._backend.supports_active_cooling

  @property
  def supports_locking(self) -> bool:
    return self._backend.supports_locking

  async def setup(self):
    await self._backend._on_setup()

  async def stop(self):
    await self._backend._on_stop()

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")
    return {"index": self._backend.index, "interface": None}

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    await self._backend.start_shaking(
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
    await self._backend.stop_shaking()

  async def get_is_shaking(self) -> bool:
    return await self._backend.request_is_shaking()

  async def lock_plate(self):
    await self._backend.lock_plate()

  async def unlock_plate(self):
    await self._backend.unlock_plate()

  async def set_temperature(self, temperature: float):
    await self._backend.set_temperature(temperature=temperature)

  async def get_current_temperature(self) -> float:
    return await self._backend.request_current_temperature()

  async def _get_current_temperature(self) -> Dict[str, float]:
    return await self._backend._request_current_temperature()

  async def get_edge_temperature(self) -> float:
    return await self._backend.request_edge_temperature()

  async def deactivate(self):
    await self._backend.deactivate()
