"""Legacy. Use pylabrobot.thermo_fisher.cytomat.HeraeusCytomatBackend instead."""

from typing import List

from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.thermo_fisher.cytomat import heraeus_backend as new_heraeus


class HeraeusCytomatBackend(IncubatorBackend):
  """Legacy. Use pylabrobot.thermo_fisher.cytomat.HeraeusCytomatBackend instead."""

  def __init__(self, port: str):
    super().__init__()
    self._new = new_heraeus.HeraeusCytomatBackend(port=port)

  @property
  def io(self):
    return self._new.io

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    await self._new.set_racks(racks)

  async def open_door(self):
    await self._new.open_door()

  async def close_door(self):
    await self._new.close_door()

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs):
    await self._new.fetch_plate_to_loading_tray(plate)

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs):
    await self._new.store_plate(plate, site)

  async def set_temperature(self, temperature: float):
    return await self._new.set_temperature(temperature)

  async def get_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def start_shaking(self, frequency: float = 1.0):
    await self._new.start_shaking(speed=frequency)

  async def stop_shaking(self):
    await self._new.stop_shaking()

  async def wait_for_transfer_station(self, occupied: bool = False):
    await self._new.wait_for_transfer_station(occupied=occupied)

  async def initialize(self):
    await self._new.initialize()

  def serialize(self) -> dict:
    return self._new.serialize()

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"])
