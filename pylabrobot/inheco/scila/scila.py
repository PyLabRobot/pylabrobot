from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device

from .scila_backend import SCILABackend


class SCILA(Device):
  """Inheco SCILA incubator with 4 drawers and temperature control."""

  def __init__(self, name: str, backend: SCILABackend):
    raise NotImplementedError("SCILA is missing resource definition.")
    Device.__init__(self, backend=backend)
    self._backend: SCILABackend = backend
    self.tc = TemperatureControlCapability(backend=backend)
    self._capabilities = [self.tc]

  def serialize(self) -> dict:
    return Device.serialize(self)
