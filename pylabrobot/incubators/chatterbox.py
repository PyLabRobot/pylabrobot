from pylabrobot.incubators.backend import IncubatorBackend
from pylabrobot.resources.carrier import PlateHolder
from pylabrobot.resources.plate import Plate


class IncubatorChatterboxBackend(IncubatorBackend):
  def __init__(self):
    self._dummy_temperature = 37.0

  async def setup(self):
    print("Setting up incubator backend")

  async def stop(self):
    print("Stopping incubator backend")

  async def open_door(self):
    print("Opening door")

  async def close_door(self):
    print("Closing door")

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    print(f"Fetching plate {plate} to loading tray")

  async def take_in_plate(self, plate: Plate, site: PlateHolder):
    print(f"Taking in plate {plate} at site {site}")

  async def set_temperature(self, temperature: float):
    print(f"Setting temperature to {temperature}")

  async def get_temperature(self) -> float:
    print("Getting temperature")
    return self._dummy_temperature

  async def start_shaking(self, frequency: float):
    print(f"Starting shaking at {frequency} Hz")

  async def stop_shaking(self):
    print("Stopping shaking")
