from pylabrobot.heating_shaking.hamilton_backend import HamiltonHeatShakerBackend
from pylabrobot.heating_shaking.heater_shaker import HeaterShaker
from pylabrobot.resources.coordinate import Coordinate


def hamilton_heater_shaker(name: str, shaker_index: int):
  return HeaterShaker(
    name=name,
    size_x=146.2,
    size_y=103.8,
    size_z=74.11,
    backend=HamiltonHeatShakerBackend(shaker_index=shaker_index),
    child_location=Coordinate(x=9.66, y=9.22, z=74.11),
  )
