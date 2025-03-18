"""A hybrid between pylabrobot.heating and pylabrobot.temperature_controlling"""

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.heating_shaking.hamilton import hamilton_heater_shaker
from pylabrobot.heating_shaking.hamilton_backend import HamiltonHeaterShakerBackend
from pylabrobot.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.heating_shaking.inheco import InhecoThermoShake
