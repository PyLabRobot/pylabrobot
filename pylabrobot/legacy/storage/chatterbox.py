from typing import List, Optional

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate


class IncubatorChatterboxBackend(IncubatorBackend):
  def __init__(self):
    super().__init__()
    self._dummy_temperature = 37.0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    print("Setting up incubator backend")
    stack.push_shielded_async_callback(self._print_stop_message)
    stack.push_shielded_async_callback(self._force_stop_shaking)

  async def _print_stop_message(self) -> None:
    print("Stopping incubator backend")

  async def open_door(self):
    print("Opening door")

  async def close_door(self):
    print("Closing door")

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs):
    print(f"Fetching plate {plate} to loading tray")

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs):
    print(f"Taking in plate {plate} at site {site}")

  async def set_temperature(self, temperature: float):
    print(f"Setting temperature to {temperature}")

  async def get_temperature(self) -> float:
    print("Getting temperature")
    return self._dummy_temperature

  async def _start_shaking_hardware(
    self, frequency: float, shakers: Optional[List[int]] = None
  ) -> None:
    print(f"Starting shaking at {frequency} Hz")

  async def _stop_shaking_hardware(self) -> None:
    print("Stopping shaking")

  async def start_shaking(self, frequency: float):
    """Start durable shaking through the shared operation owner."""
    return await super().start_shaking(frequency)

  async def stop_shaking(self):
    """Stop durable shaking through the shared operation owner."""
    return await super().stop_shaking()
