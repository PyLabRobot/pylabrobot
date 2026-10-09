from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.heating_shaking import HeaterShakerBackend
from pylabrobot.legacy.shaking import ShakerChatterboxBackend
from pylabrobot.legacy.temperature_controlling import TemperatureControllerChatterboxBackend


class HeaterShakerChatterboxBackend(
  HeaterShakerBackend, ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
