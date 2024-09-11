from pylabrobot.tilting import TilterBackend


class TilterChatterboxBackend(TilterBackend):
  async def set_angle(self, angle: float):
    print(f"Setting the angle to {angle}.")
