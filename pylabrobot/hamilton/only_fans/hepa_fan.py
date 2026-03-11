from typing import Optional

from pylabrobot.capabilities.fan_control import FanControlCapability
from pylabrobot.machines import Machine

from .backend import HamiltonHepaFanBackend


class HamiltonHepaFan(Machine):
  """Hamilton HEPA fan attachment."""

  def __init__(self, name: str, device_id: Optional[str] = None):
    backend = HamiltonHepaFanBackend(device_id=device_id)
    super().__init__(backend=backend)
    self._backend: HamiltonHepaFanBackend = backend
    self.fan = FanControlCapability(backend=backend)
    self._capabilities = [self.fan]
