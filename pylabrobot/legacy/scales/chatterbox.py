from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.scales.scale_backend import ScaleBackend


class ScaleChatterboxBackend(ScaleBackend):
  """Chatter box backend for device-free testing. Prints out all operations."""

  def __init__(self, dummy_weight: float = 0.0) -> None:
    super().__init__()
    self._dummy_weight = dummy_weight

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter the resource-free chatterbox lifespan."""
    await super()._enter_lifespan(stack)
    print("Setting up the scale.")
    stack.callback(self._print_stop_message)

  def _print_stop_message(self) -> None:
    """Print the chatterbox lifespan cleanup message."""
    print("Stopping the scale.")

  async def tare(self):
    print("Taring the scale")

  async def read_weight(self) -> float:
    print("Reading the weight")
    return self._dummy_weight

  async def zero(self):
    print("Zeroing the scale")
