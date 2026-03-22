"""A hybrid between pylabrobot.shaking and pylabrobot.temperature_controlling"""

from pylabrobot.legacy.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.legacy.heating_shaking.bioshake_backend import BioShake
from pylabrobot.legacy.heating_shaking.chatterbox import HeaterShakerChatterboxBackend
from pylabrobot.legacy.heating_shaking.hamilton_backend import (
  HamiltonHeaterShakerBackend,
  HamiltonHeaterShakerBox,
)
from pylabrobot.legacy.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.legacy.heating_shaking.inheco.thermoshake import (
  inheco_thermoshake,
  inheco_thermoshake_ac,
  inheco_thermoshake_rm,
)
from pylabrobot.legacy.heating_shaking.inheco.thermoshake_backend import InhecoThermoshakeBackend
