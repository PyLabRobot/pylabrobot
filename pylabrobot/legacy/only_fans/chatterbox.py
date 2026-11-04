from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.only_fans import FanBackend


class FanChatterboxBackend(FanBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up the fan.")
    stack.push_shielded_async_callback(self._print_stop_message)

  async def _print_stop_message(self) -> None:
    print("Stopping the fan.")

  async def turn_on(self, intensity: int) -> None:
    print(f"Turning on the fan at intensity {intensity}.")

  async def turn_off(self) -> None:
    print("Turning off the fan.")
