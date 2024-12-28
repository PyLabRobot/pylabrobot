from pylabrobot.shaking import ShakerBackend


class ShakerChatterboxBackend(ShakerBackend):
  """Backend for a shaker machine"""

  temperature: float = 0

  async def shake(self, speed: float):
    print("Shaking at speed", speed)

  async def stop_shaking(self):
    print("Stopping shaking")

  async def lock_plate(self):
    print("Locking plate")

  async def unlock_plate(self):
    print("Unlocking plate")
