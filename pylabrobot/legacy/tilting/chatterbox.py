from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.tilting import TilterBackend


class TilterChatterboxBackend(TilterBackend):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up tilter.")
    stack.push_shielded_async_callback(self._print_stop_message)

  async def _print_stop_message(self) -> None:
    print("Stopping tilter.")

  async def set_angle(self, angle: float):
    print(f"Setting the angle to {angle}.")
