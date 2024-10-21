from pylabrobot.shaking import ShakerBackend


class ShakerChatterboxBackend(ShakerBackend):
  """ Backend for a shaker machine """

  async def shake(self, speed: float):
    print("Shaking at speed", speed)

  async def stop_shaking(self):
    print("Stopping shaking")
