from pylabrobot.tilting import TilterBackend


class TilterChatterboxBackend(TilterBackend):
  async def setup(self):
    print("Setting up tilter.")

  async def stop(self):
    print("Stopping tilter.")

  async def set_angle(self, angle: float):
    print(f"Setting the angle to {angle}.")

