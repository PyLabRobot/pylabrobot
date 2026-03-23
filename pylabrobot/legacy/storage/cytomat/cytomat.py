"""Legacy. Use pylabrobot.thermo_fisher.cytomat.CytomatBackend instead."""

from typing import List, Optional, Union

from pylabrobot.resources import Plate, PlateCarrier, PlateHolder
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.legacy.storage.cytomat.constants import CytomatType
from pylabrobot.thermo_fisher.cytomat import backend as new_cytomat


class CytomatBackend(IncubatorBackend):
  """Legacy. Use pylabrobot.thermo_fisher.cytomat.CytomatBackend instead."""

  def __init__(self, model: Union[CytomatType, str], port: str):
    super().__init__()
    self._new = new_cytomat.CytomatBackend(model=model, port=port)

  @property
  def model(self):
    return self._new.model

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
    return await self._new.open_door()

  async def close_door(self):
    return await self._new.close_door()

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs):
    await self._new.fetch_plate_to_loading_tray(plate)

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs):
    await self._new.store_plate(plate, site)

  async def set_temperature(self, *args, **kwargs):
    return await self._new.set_temperature(*args, **kwargs)

  async def get_temperature(self) -> float:
    return await self._new.get_current_temperature()

  async def start_shaking(self, frequency: float, shakers: Optional[List[int]] = None):
    return await self._new.start_shaking(speed=frequency, shakers=shakers)

  async def stop_shaking(self):
    return await self._new.stop_shaking()

  # Device-specific methods delegated to new backend

  def _assemble_command(self, command_type: str, command: str, params: str):
    return self._new._assemble_command(command_type, command, params)

  async def send_command(self, command_type: str, command: str, params: str) -> str:
    return await self._new.send_command(command_type, command, params)

  async def send_action(self, command_type, command, params, timeout=60):
    return await self._new.send_action(command_type, command, params, timeout=timeout)

  async def get_overview_register(self):
    return await self._new.get_overview_register()

  async def get_warning_register(self):
    return await self._new.get_warning_register()

  async def get_error_register(self):
    return await self._new.get_error_register()

  async def reset_error_register(self):
    return await self._new.reset_error_register()

  async def initialize(self):
    return await self._new.initialize()

  async def shovel_in(self):
    return await self._new.shovel_in()

  async def shovel_out(self):
    return await self._new.shovel_out()

  async def get_action_register(self):
    return await self._new.get_action_register()

  async def get_swap_register(self):
    return await self._new.get_swap_register()

  async def get_sensor_register(self):
    return await self._new.get_sensor_register()

  async def action_transfer_to_storage(self, site):
    return await self._new.action_transfer_to_storage(site)

  async def action_storage_to_transfer(self, site):
    return await self._new.action_storage_to_transfer(site)

  async def action_storage_to_wait(self, site):
    return await self._new.action_storage_to_wait(site)

  async def action_wait_to_storage(self, site):
    return await self._new.action_wait_to_storage(site)

  async def action_wait_to_transfer(self):
    return await self._new.action_wait_to_transfer()

  async def action_transfer_to_wait(self):
    return await self._new.action_transfer_to_wait()

  async def action_wait_to_exposed(self):
    return await self._new.action_wait_to_exposed()

  async def action_exposed_to_wait(self):
    return await self._new.action_exposed_to_wait()

  async def action_exposed_to_storage(self, site):
    return await self._new.action_exposed_to_storage(site)

  async def action_storage_to_exposed(self, site):
    return await self._new.action_storage_to_exposed(site)

  async def action_read_barcode(self, site_number_a, site_number_b):
    return await self._new.action_read_barcode(site_number_a, site_number_b)

  async def wait_for_transfer_station(self, occupied=False):
    return await self._new.wait_for_transfer_station(occupied=occupied)

  async def wait_for_task_completion(self, timeout=60):
    return await self._new.wait_for_task_completion(timeout=timeout)

  async def init_shakers(self):
    return await self._new.init_shakers()

  async def set_shaking_frequency(self, frequency, shakers=None):
    return await self._new.set_shaking_frequency(frequency, shakers)

  async def get_incubation_query(self, query):
    return await self._new.get_incubation_query(query)

  async def get_co2(self):
    return await self._new.get_co2()

  async def get_humidity(self):
    return await self._new.get_humidity()

  async def get_o2(self):
    return await self._new.get_o2()

  def serialize(self) -> dict:
    return self._new.serialize()


class CytomatChatterbox(CytomatBackend):
  """Legacy. Use pylabrobot.thermo_fisher.cytomat.CytomatChatterbox instead."""

  def __init__(self, model: Union[CytomatType, str], port: str):
    # Skip CytomatBackend.__init__ and use the new chatterbox directly
    IncubatorBackend.__init__(self)
    from pylabrobot.thermo_fisher.cytomat.chatterbox import CytomatChatterbox as NewChatterbox
    self._new = NewChatterbox(model=model, port=port)


class Cytomat:
  def __init__(self, *args, **kwargs):
    raise RuntimeError("`Cytomat` is deprecated. Please use `CytomatBackend` instead. ")
