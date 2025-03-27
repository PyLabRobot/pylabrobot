from pylabrobot.shaking.chatterbox import ShakerChatterboxBackend
from pylabrobot.temperature_controlling.chatterbox import (
  TemperatureControllerChatterboxBackend,
)


class HeaterShakerChatterboxBackend(
  ShakerChatterboxBackend, TemperatureControllerChatterboxBackend
):
  pass
