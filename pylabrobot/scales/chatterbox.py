from pylabrobot.scales.scale_backend import ScaleBackend


class ScaleChatterboxBackend(ScaleBackend):
  """ Chatter box backend for device-free testing. Prints out all operations. """

  def __init__(self, dummy_weight: float = 0.0) -> None:
    self._dummy_weight = dummy_weight

  async def setup(self) -> None:
    print("Setting up the scale.")

  async def stop(self) -> None:
    print("Stopping the scale.")

  async def tare(self):
    print("Taring the scale")

  async def get_weight(self) -> float:
    print("Getting the weight")
    return self._dummy_weight

  async def zero(self):
    print("Zeroing the scale")
