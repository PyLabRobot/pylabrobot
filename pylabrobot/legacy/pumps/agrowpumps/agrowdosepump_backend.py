"""Legacy. Use pylabrobot.agrowpumps instead."""

from typing import Dict, List, Union

from pylabrobot.agrowpumps.agrowdosepump_backend import AgrowChannelBackend, AgrowDriver
from pylabrobot.legacy.pumps.backend import PumpArrayBackend


class AgrowPumpArrayBackend(PumpArrayBackend):
  """Legacy. Use pylabrobot.agrowpumps.AgrowDosePumpArray instead."""

  def __init__(self, port: str, address: Union[int, str]):
    self.driver = AgrowDriver(port=port, address=address)
    self._backends: List[AgrowChannelBackend] = []

  @property
  def port(self):
    return self.driver.port

  @property
  def address(self):
    return self.driver.address

  @property
  def modbus(self):
    return self.driver.modbus

  @property
  def num_channels(self) -> int:
    return self.driver.num_channels

  @property
  def pump_index_to_address(self) -> Dict[int, int]:
    return self.driver.pump_index_to_address

  async def setup(self):
    await self.driver.setup()
    self._backends = [
      AgrowChannelBackend(self.driver, ch) for ch in range(self.driver.num_channels)
    ]

  async def stop(self):
    await self.halt()
    await self.driver.stop()

  def serialize(self):
    return {
      **super().serialize(),
      "port": self.port,
      "address": self.address,
    }

  async def run_revolutions(self, num_revolutions: List[float], use_channels: List[int]):
    raise NotImplementedError(
      "Revolution based pumping commands are not available for this pump array."
    )

  async def run_continuously(self, speed: List[float], use_channels: List[int]):
    for channel, pump_speed in zip(use_channels, speed):
      await self._backends[channel].run_continuously(pump_speed)

  async def halt(self):
    for backend in self._backends:
      await backend.halt()


# Deprecated alias
class AgrowPumpArray:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`AgrowPumpArray` is deprecated. Please use `AgrowPumpArrayBackend` instead."
    )
