"""Legacy. Use pylabrobot.hamilton.only_fans.HamiltonHepaFan instead."""

from pylabrobot.hamilton.only_fans.backend import HamiltonHepaFanDriver, HamiltonHepaFanFanBackend
from pylabrobot.legacy.only_fans.backend import FanBackend


class HamiltonHepaFanBackend(FanBackend):
  """Legacy. Use pylabrobot.hamilton.only_fans.HamiltonHepaFan instead."""

  def __init__(self, device_id=None):
    self._driver = HamiltonHepaFanDriver(device_id=device_id)
    self._fan = HamiltonHepaFanFanBackend(self._driver)

  async def setup(self) -> None:
    await self._driver.setup()

  async def turn_on(self, intensity: int) -> None:
    await self._fan.turn_on(intensity=intensity)

  async def turn_off(self) -> None:
    await self._fan.turn_off()

  async def stop(self) -> None:
    await self._driver.stop()


class HamiltonHepaFan:
  """Deprecated. Use HamiltonHepaFanBackend instead."""

  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`HamiltonHepaFan` is deprecated. Please use `HamiltonHepaFanBackend` instead."
    )
