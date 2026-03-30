from typing import Optional

from pylabrobot.capabilities.fan_control import FanControlCapability
from pylabrobot.device import Device

from .backend import HamiltonHepaFanDriver, HamiltonHepaFanFanBackend


class HamiltonHepaFan(Device):
  """Hamilton HEPA fan attachment."""

  def __init__(self, name: str, device_id: Optional[str] = None):
    driver = HamiltonHepaFanDriver(device_id=device_id)
    super().__init__(driver=driver)
    self._driver: HamiltonHepaFanDriver = driver
    self.fan = FanControlCapability(backend=HamiltonHepaFanFanBackend(driver))
    self._capabilities = [self.fan]
