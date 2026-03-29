from typing import Optional

from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device

from .scila_backend import SCILADriver, SCILATemperatureBackend


class SCILA(Device):
  """Inheco SCILA incubator with 4 drawers and temperature control."""

  def __init__(self, name: str, scila_ip: str, client_ip: Optional[str] = None):
    raise NotImplementedError("SCILA is missing resource definition.")
    driver = SCILADriver(scila_ip=scila_ip, client_ip=client_ip)
    Device.__init__(self, driver=driver)
    self._driver: SCILADriver = driver
    self.tc = TemperatureController(backend=SCILATemperatureBackend(driver=driver))
    self._capabilities = [self.tc]

  def serialize(self) -> dict:
    return Device.serialize(self)
