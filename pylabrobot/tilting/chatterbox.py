import contextlib

from pylabrobot.tilting import TilterBackend


class TilterChatterboxBackend(TilterBackend):
  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack):
    print("Setting up tilter.")

    def _cleanup():
      print("Stopping tilter.")

    stack.callback(_cleanup)

  async def set_angle(self, angle: float):
    print(f"Setting the angle to {angle}.")
