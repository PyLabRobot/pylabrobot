import abc
import warnings

from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.temperature_controlling.inheco.control_box import InhecoTECControlBox


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


# Deprecated alias with warning # TODO: remove mid May 2025 (giving people 1 month to update)
# https://github.com/PyLabRobot/pylabrobot/issues/466


class InhecoThermoShake:
  def __init__(self, *args, **kwargs):
    raise RuntimeError(
      "`InhecoThermoShake` is deprecated. Please use `InhecoThermoShakeBackend` instead. "
    )
