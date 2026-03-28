"""Chatterbox scale backend for device-free testing and simulation."""

from pylabrobot.scales.scale_backend import ScaleBackend


class ScaleChatterboxBackend(ScaleBackend):
  """Chatter box backend for device-free testing.

  Simulates scale behavior: tracks zero offset, tare weight, and platform load.
  The total sensor reading is ``platform_weight + sample_weight``.
  ``read_weight`` returns the net: ``platform_weight + sample_weight - zero_offset - tare_weight``.

  Set ``platform_weight`` to simulate a container or vessel on the scale.
  Set ``sample_weight`` to simulate material added to the container.

  Example - zero::

    backend = ScaleChatterboxBackend()
    backend.platform_weight = 2.0    # residue on empty platform
    await scale.zero()               # zero_offset = 2.0
    await scale.read_weight()        # returns 0.0
    backend.platform_weight = 52.0   # place a 50g beaker
    await scale.read_weight()        # returns 50.0

  Example - tare::

    backend = ScaleChatterboxBackend()
    backend.platform_weight = 50.0   # place a 50g beaker
    await scale.tare()               # tare_weight = 50.0
    backend.sample_weight = 10.0     # add 10g of liquid
    await scale.read_weight()        # returns 10.0
    await scale.request_tare_weight()  # returns 50.0
  """

  def __init__(self) -> None:
    super().__init__()
    self.platform_weight: float = 0.0
    self.sample_weight: float = 0.0
    self.zero_offset: float = 0.0
    self.tare_weight: float = 0.0

  @property
  def _sensor_reading(self) -> float:
    return self.platform_weight + self.sample_weight

  async def setup(self) -> None:
    print("Setting up the scale.")

  async def stop(self) -> None:
    print("Stopping the scale.")

  async def zero(self, **kwargs):
    print("Zeroing the scale")
    self.zero_offset = self._sensor_reading

  async def tare(self, **kwargs):
    print("Taring the scale")
    self.tare_weight = self._sensor_reading - self.zero_offset

  async def request_tare_weight(self, **kwargs) -> float:
    print("Requesting tare weight")
    return round(self.tare_weight, 5)

  async def read_weight(self, **kwargs) -> float:
    print("Reading the weight")
    return round(self._sensor_reading - self.zero_offset - self.tare_weight, 5)
