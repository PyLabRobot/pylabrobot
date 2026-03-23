"""Legacy. Use pylabrobot.hamilton.hepa_fan.HamiltonHepaFanBackend instead."""

from pylabrobot.hamilton.only_fans import backend as hepa_fan_backend
from pylabrobot.legacy.only_fans.backend import FanBackend


class HamiltonHepaFanBackend(FanBackend):
  """Legacy. Use pylabrobot.hamilton.hepa_fan.HamiltonHepaFanBackend instead."""

  def __init__(self, device_id=None):
    self._new = hepa_fan_backend.HamiltonHepaFanBackend(device_id=device_id)

  async def setup(self) -> None:
    await self._new.setup()

  async def turn_on(self, intensity: int) -> None:
    await self._new.turn_on(intensity=intensity)

  async def turn_off(self) -> None:
    await self._new.turn_off()

  async def stop(self) -> None:
    await self._new.stop()


class HamiltonHepaFan:
  """Deprecated. Use HamiltonHepaFanBackend instead."""

  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`HamiltonHepaFan` is deprecated. Please use `HamiltonHepaFanBackend` instead."
    )
