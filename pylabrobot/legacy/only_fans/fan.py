"""Legacy. Use pylabrobot.hamilton.only_fans.HamiltonHepaFan instead."""

from pylabrobot.capabilities.fan_control import FanControlCapability
from pylabrobot.legacy.machines.machine import Machine

from .backend import FanBackend


class Fan(Machine):
  """Legacy. Use a vendor-specific machine class instead."""

  def __init__(self, backend: FanBackend):
    super().__init__(backend=backend)
    self._backend: FanBackend = backend
    self._cap = FanControlCapability(backend=backend)

  async def setup(self, **backend_kwargs):
    await super().setup(**backend_kwargs)
    await self._cap._on_setup()

  async def turn_on(self, intensity: int, duration=None):
    await self._cap.turn_on(intensity=intensity, duration=duration)

  async def turn_off(self):
    await self._cap.turn_off()

  async def stop(self):
    await self._cap._on_stop()
    await super().stop()
