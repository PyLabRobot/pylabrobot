import contextlib

from pylabrobot.scales.scale_backend import ScaleBackend


class ScaleChatterboxBackend(ScaleBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  def __init__(self, dummy_weight: float = 0.0) -> None:
    self._dummy_weight = dummy_weight
    super().__init__()

  async def _enter_lifespan(self, stack: contextlib.AsyncExitStack):
    print("Setting up the scale.")

    def _cleanup():
      print("Stopping the scale.")

    stack.callback(_cleanup)

  async def tare(self):
    print("Taring the scale")

  async def read_weight(self) -> float:
    print("Reading the weight")
    return self._dummy_weight

  async def zero(self):
    print("Zeroing the scale")
