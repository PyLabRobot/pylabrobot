"""A hybrid between pylabrobot.shaking and pylabrobot.temperature_controlling"""

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.heating_shaking.chatterbox import HeaterShakerChatterboxBackend
from pylabrobot.heating_shaking.hamilton import HamiltonHeatShaker
from pylabrobot.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.heating_shaking.inheco import InhecoThermoShakeBackend
