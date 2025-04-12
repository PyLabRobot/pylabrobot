from pylabrobot.heating_shaking import HeaterShakerBackend
from pylabrobot.shaking import ShakerChatterboxBackend
from pylabrobot.temperature_controlling import TemperatureControllerChatterboxBackend


class HeaterShakerChatterboxBackend(
  HeaterShakerBackend, ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  async def initialize_shaker_drive(self):
    print("initialize_shaker_drive")