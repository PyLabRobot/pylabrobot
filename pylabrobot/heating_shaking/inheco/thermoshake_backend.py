import warnings

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.temperature_controlling.inheco.temperature_controller import (
  InhecoTemperatureControllerBackend,
)


class InhecoThermoshakeBackend(InhecoTemperatureControllerBackend, HeaterShakerBackend):
  """Backend for Inheco Thermoshake devices

  https://www.inheco.com/thermoshake-ac.html
  """

  async def stop(self):
    await self.stop_shaking()
    await super().stop()

  async def _start_shaking_command(self):
    """Start shaking the device at the speed set by `set_shaker_speed`"""

    return await self.interface.send_command(f"{self.index}ASE1")

  async def stop_shaking(self):
    """Stop shaking the device"""

    return await self.interface.send_command(f"{self.index}ASE0")

  async def set_shaker_speed(self, speed: float):
    """Set the shaker speed on the device, but do not start shaking yet. Use `start_shaking` for
    that.
    """

    # # 60 ... 2000
    # # Thermoshake and Teleshake
    assert speed in range(60, 2001), "Speed must be in the range 60 to 2000 RPM"

    # Thermoshake AC, Teleshake95 AC and Teleshake AC
    # 150 ... 3000
    # assert speed in range(150, 3001), "Speed must be in the range 150 to 3000 RPM"

    return await self.interface.send_command(f"1SSR{speed}")

  async def set_shaker_shape(self, shape: int):
    """Set the shape of the figure that should be shaked.

    Args:
      shape: 0 = Circle anticlockwise, 1 = Circle clockwise, 2 = Up left down right, 3 = Up right
        down left, 4 = Up-down, 5 = Left-right
    """

    assert shape in range(6), "Shape must be in the range 0 to 5"

    return await self.interface.send_command(f"1SSS{shape}")

  async def start_shaking(self, speed: float, shape: int = 0):
    """Shake the shaker at the given speed

    Args:
      speed: Speed of shaking in revolutions per minute (RPM)
    """

    await self.set_shaker_speed(speed=speed)
    await self.set_shaker_shape(shape=shape)
    await self._start_shaking_command()

  async def shake(self, speed: float, shape: int = 0):
    """Deprecated alias for ``start_shaking``."""
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
    raise NotImplementedError(
      "Locking the plate is not implemented yet for Inheco ThermoShake devices. "
    )

  async def unlock_plate(self):
    raise NotImplementedError(
      "Unlocking the plate is not implemented yet for Inheco ThermoShake devices. "
    )
