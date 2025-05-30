from pylabrobot.heating_shaking import HeaterShakerBackend
from pylabrobot.shaking import ShakerChatterboxBackend
from pylabrobot.temperature_controlling import TemperatureControllerChatterboxBackend


class HeaterShakerChatterboxBackend(
  HeaterShakerBackend, ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  @property
  def supports_active_cooling(self) -> bool:
    return False
