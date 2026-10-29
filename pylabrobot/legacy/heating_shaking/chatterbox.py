from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.heating_shaking import HeaterShakerBackend
from pylabrobot.legacy.shaking import ShakerChatterboxBackend
from pylabrobot.legacy.temperature_controlling import TemperatureControllerChatterboxBackend


class HeaterShakerChatterboxBackend(
  HeaterShakerBackend, ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    # The device announces its lifespan once, as a shaker. Entering through
    # ShakerChatterboxBackend would also run the temperature controller's chatter, and its
    # stop message shares a name with the shaker's, so the shaker's would be pushed twice.
    await super(TemperatureControllerChatterboxBackend, self)._enter_lifespan(stack)
    print("Setting up shaker")
    stack.push_shielded_async_callback(self._print_stop_message)
