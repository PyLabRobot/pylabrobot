from typing import Optional

from pylabrobot.capabilities.fan_control import FanControlCapability
from pylabrobot.device import Device

from .backend import HamiltonHepaFanBackend


class HamiltonHepaFan(Device):
  """Hamilton HEPA fan attachment."""

  def __init__(self, name: str, device_id: Optional[str] = None):
    backend = HamiltonHepaFanBackend(device_id=device_id)
    super().__init__(driver=backend)
    self._driver: HamiltonHepaFanBackend = backend
    self.fan = FanControlCapability(backend=backend)
    self._capabilities = [self.fan]
