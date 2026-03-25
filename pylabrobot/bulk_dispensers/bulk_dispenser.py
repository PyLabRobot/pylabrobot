from __future__ import annotations

from pylabrobot.bulk_dispensers.backend import BulkDispenserBackend
from pylabrobot.machines.machine import Machine, need_setup_finished


class BulkDispenser(Machine):
  """Frontend for bulk reagent dispensers."""

  def __init__(self, backend: BulkDispenserBackend) -> None:
    super().__init__(backend=backend)
    self.backend: BulkDispenserBackend = backend

  @need_setup_finished
  async def dispense(self, **backend_kwargs) -> None:
    await self.backend.dispense(**backend_kwargs)

  @need_setup_finished
  async def prime(self, volume: float, **backend_kwargs) -> None:
    await self.backend.prime(volume=volume, **backend_kwargs)

  @need_setup_finished
  async def empty(self, volume: float, **backend_kwargs) -> None:
    await self.backend.empty(volume=volume, **backend_kwargs)

  @need_setup_finished
  async def shake(self, time: float, distance: int, speed: int, **backend_kwargs) -> None:
    await self.backend.shake(time=time, distance=distance, speed=speed, **backend_kwargs)

  @need_setup_finished
  async def move_plate_out(self, **backend_kwargs) -> None:
    await self.backend.move_plate_out(**backend_kwargs)

  @need_setup_finished
  async def set_plate_type(self, plate_type: int, **backend_kwargs) -> None:
    await self.backend.set_plate_type(plate_type=plate_type, **backend_kwargs)

  @need_setup_finished
  async def set_cassette_type(self, cassette_type: int, **backend_kwargs) -> None:
    await self.backend.set_cassette_type(cassette_type=cassette_type, **backend_kwargs)

  @need_setup_finished
  async def set_column_volume(self, column: int, volume: float, **backend_kwargs) -> None:
    await self.backend.set_column_volume(column=column, volume=volume, **backend_kwargs)

  @need_setup_finished
  async def set_dispensing_height(self, height: int, **backend_kwargs) -> None:
    await self.backend.set_dispensing_height(height=height, **backend_kwargs)

  @need_setup_finished
  async def set_pump_speed(self, speed: int, **backend_kwargs) -> None:
    await self.backend.set_pump_speed(speed=speed, **backend_kwargs)

  @need_setup_finished
  async def set_dispensing_order(self, order: int, **backend_kwargs) -> None:
    await self.backend.set_dispensing_order(order=order, **backend_kwargs)

  @need_setup_finished
  async def abort(self, **backend_kwargs) -> None:
    await self.backend.abort(**backend_kwargs)
