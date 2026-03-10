import abc
import warnings
from typing import Optional

from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
  TemperatureControllerBackend,
)
from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, ResourceHolder

from .control_box import InhecoTECControlBox


class InhecoTemperatureControllerBackend(TemperatureControllerBackend, metaclass=abc.ABCMeta):
  """Universal backend for Inheco Temperature Controller devices such as ThermoShake and CPAC"""

  @property
  def supports_active_cooling(self) -> bool:
    return True

  def __init__(self, index: int, control_box: InhecoTECControlBox):
    assert 1 <= index <= 6, "Index must be between 1 and 6 (inclusive)"
    self.index = index
    self.interface = control_box

  async def setup(self):
    pass

  async def stop(self):
    await self.stop_temperature_control()

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")
    return super().serialize()

  # -- temperature control

  async def set_temperature(self, temperature: float):
    await self.set_target_temperature(temperature)
    await self.start_temperature_control()

  async def get_current_temperature(self) -> float:
    response = await self.interface.send_command(f"{self.index}RAT0")
    return float(response) / 10

  async def deactivate(self):
    await self.stop_temperature_control()

  # --- firmware temp

  async def set_target_temperature(self, temperature: float):
    temperature = int(temperature * 10)
    await self.interface.send_command(f"{self.index}STT{temperature}")

  async def start_temperature_control(self):
    """Start the temperature control"""
    return await self.interface.send_command(f"{self.index}ATE1")

  async def stop_temperature_control(self):
    """Stop the temperature control"""
    return await self.interface.send_command(f"{self.index}ATE0")

  # --- firmware misc

  async def get_device_info(self, info_type: int):
    """Get device information

    - 0 Bootstrap Version
    - 1 Application Version
    - 2 Serial number
    - 3 Current hardware version
    - 4 INHECO copyright
    """

    assert info_type in range(5), "Info type must be in the range 0 to 4"
    return await self.interface.send_command(f"{self.index}RFV{info_type}")


class InhecoCPACBackend(InhecoTemperatureControllerBackend):
  pass


class InhecoCPAC(ResourceHolder, Machine):
  """Inheco CPAC temperature controller.

  Example:
    >>> from pylabrobot.inheco import InhecoCPAC, inheco_cpac_ultraflat
    >>> cpac = inheco_cpac_ultraflat("cpac", control_box=box, index=1)
    >>> await cpac.setup()
    >>> await cpac.tc.set_temperature(37.0)
    >>> await cpac.tc.get_temperature()
    37.0
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: InhecoCPACBackend,
    child_location: Coordinate,
    category: str = "temperature_controller",
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self._backend: InhecoCPACBackend = backend
    self.tc = TemperatureControlCapability(backend=backend)
    self._capabilities = [self.tc]

  def serialize(self) -> dict:
    return {
      **Machine.serialize(self),
      **ResourceHolder.serialize(self),
    }


def inheco_cpac_ultraflat(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoCPAC:
  """Inheco CPAC Ultraflat
  7000166, 7000190, 7000165

  https://www.inheco.com/data/pdf/cpac-brochure-1013-1032-34.pdf
  """

  return InhecoCPAC(
    name=name,
    backend=InhecoCPACBackend(control_box=control_box, index=index),
    size_x=113,  # from spec
    size_y=89,  # from spec
    size_z=129,  # from spec
    child_location=Coordinate(x=8, y=11, z=77),  # x from spec, y and z measured
    model=inheco_cpac_ultraflat.__name__,
  )
