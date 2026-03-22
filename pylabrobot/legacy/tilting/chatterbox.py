"""Legacy. Use pylabrobot.capabilities.tilting instead."""

from pylabrobot.capabilities.tilting import TilterBackend


class TilterChatterboxBackend(TilterBackend):
  async def setup(self):
    pass

  async def stop(self):
    pass

  async def set_angle(self, angle: float):
    pass
