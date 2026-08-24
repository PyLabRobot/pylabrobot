from pylabrobot.legacy.heating_shaking import HeaterShakerBackend
from pylabrobot.legacy.shaking import ShakerChatterboxBackend
from pylabrobot.legacy.temperature_controlling import TemperatureControllerChatterboxBackend


class HeaterShakerChatterboxBackend(
  HeaterShakerBackend, ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  pass
