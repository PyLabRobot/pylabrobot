from pylabrobot.shaking.backend import ShakerBackend
from pylabrobot.temperature_controlling.backend import TemperatureControllerBackend


class HeaterShakerBackend(ShakerBackend, TemperatureControllerBackend):
  """ Heater shaker backend: a union of ShakerBackend and TemperatureControllerBackend """
