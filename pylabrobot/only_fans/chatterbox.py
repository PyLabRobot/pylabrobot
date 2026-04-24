from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.only_fans import FanBackend


class FanChatterboxBackend(FanBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    print("Setting up the fan.")

    def cleanup():
      print("Stopping the fan.")

    stack.callback(cleanup)

  async def turn_on(self, intensity: int) -> None:
    print(f"Turning on the fan at intensity {intensity}.")

  async def turn_off(self) -> None:
    print("Turning off the fan.")
