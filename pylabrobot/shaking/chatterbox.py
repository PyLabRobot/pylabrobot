from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.shaking import ShakerBackend


class ShakerChatterboxBackend(ShakerBackend):
  """Backend for a shaker machine"""

  temperature: float = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    await super()._enter_lifespan(stack)
    print("Setting up shaker")
    stack.callback(lambda: print("Stopping shaker"))

  async def start_shaking(self, speed: float):
    print("Shaking at speed", speed)

  async def stop_shaking(self):
    print("Stopping shaking")

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    print("Locking plate")

  async def unlock_plate(self):
    print("Unlocking plate")
