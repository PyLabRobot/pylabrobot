import warnings
from typing import Optional

from pylabrobot.capabilities.shaking import ShakingCapability, ShakerBackend
from pylabrobot.capabilities.temperature_controlling import TemperatureControlCapability
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, ResourceHolder

from .control_box import InhecoTECControlBox
from .cpac import InhecoTemperatureControllerBackend


class InhecoThermoshakeBackend(InhecoTemperatureControllerBackend, ShakerBackend):
  """Backend for Inheco Thermoshake devices.

  https://www.inheco.com/thermoshake-ac.html
  """

  async def stop(self):
    await self.stop_shaking()
    await super().stop()

  async def _start_shaking_command(self):
    return await self.interface.send_command(f"{self.index}ASE1")

  async def stop_shaking(self):
    return await self.interface.send_command(f"{self.index}ASE0")

  async def set_shaker_speed(self, speed: float):
    assert speed in range(60, 2001), "Speed must be in the range 60 to 2000 RPM"
    return await self.interface.send_command(f"1SSR{speed}")

  async def set_shaker_shape(self, shape: int):
    """Set the shaking shape.

    Args:
      shape: 0 = Circle anticlockwise, 1 = Circle clockwise, 2 = Up left down right,
        3 = Up right down left, 4 = Up-down, 5 = Left-right
    """
    assert shape in range(6), "Shape must be in the range 0 to 5"
    return await self.interface.send_command(f"1SSS{shape}")

  async def start_shaking(self, speed: float, shape: int = 0):
    await self.set_shaker_speed(speed=speed)
    await self.set_shaker_shape(shape=shape)
    await self._start_shaking_command()

  async def shake(self, speed: float, shape: int = 0):
    """Deprecated alias for start_shaking."""
    warnings.warn(
      "InhecoThermoshakeBackend.shake() is deprecated. Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.start_shaking(speed=speed, shape=shape)

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    raise NotImplementedError("Locking is not supported on Inheco ThermoShake devices.")

  async def unlock_plate(self):
    raise NotImplementedError("Unlocking is not supported on Inheco ThermoShake devices.")


class InhecoThermoShake(ResourceHolder, Device):
  """Inheco ThermoShake: combined temperature control and shaking.

  Example:
    >>> from pylabrobot.inheco import InhecoThermoShake, inheco_thermoshake
    >>> ts = inheco_thermoshake("ts", control_box=box, index=1)
    >>> await ts.setup()
    >>> await ts.tc.set_temperature(37.0)
    >>> await ts.shaking.shake(speed=300)
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: InhecoThermoshakeBackend,
    child_location: Coordinate,
    category: str = "heating_shaking",
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
    Device.__init__(self, backend=backend)
    self._backend: InhecoThermoshakeBackend = backend
    self.tc = TemperatureControlCapability(backend=backend)
    self.shaker = ShakingCapability(backend=backend)
    self._capabilities = [self.tc, self.shaker]

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **ResourceHolder.serialize(self),
    }


def inheco_thermoshake_ac(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake AC

  7100160, 7100161

  https://www.inheco.com/thermoshake-ac.html
  """

  raise NotImplementedError("Inheco ThermoShake AC is missing child_location.")

  return InhecoThermoShake(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=115.9,  # from spec
    child_location=Coordinate(x=0, y=0, z=109.9),  # TODO
    model=inheco_thermoshake_ac.__name__,
  )


def inheco_thermoshake(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake (7100146)

  https://www.inheco.com/thermoshake-classic.html
  """

  return InhecoThermoShake(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=118,  # from spec
    child_location=Coordinate(x=9.62, y=9.22, z=109.9),  # measured
    model=inheco_thermoshake.__name__,
    # pedestal_size_z=-4.2,  # measured
  )


def inheco_thermoshake_rm(
  name: str, control_box: InhecoTECControlBox, index: int
) -> InhecoThermoShake:
  """Inheco Thermoshake RM (7100144)

  https://www.inheco.com/thermoshake-classic.html
  """

  raise NotImplementedError("Inheco Thermoshake RM is missing child_location")

  return InhecoThermoShake(
    name=name,
    backend=InhecoThermoshakeBackend(control_box=control_box, index=index),
    size_x=147,  # from spec
    size_y=104,  # from spec
    size_z=116,  # from spec
    child_location=Coordinate(x=0, y=0, z=0),  # TODO
    model=inheco_thermoshake_rm.__name__,
  )
