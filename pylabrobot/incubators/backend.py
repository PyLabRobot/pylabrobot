from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources import Plate, PlateCarrier, PlateHolder


class IncubatorBackend(MachineBackend, metaclass=ABCMeta):
  def __init__(self):
    self._racks: Optional[List[PlateCarrier]] = None

  @property
  def racks(self) -> List[PlateCarrier]:
    assert self._racks is not None, "Backend not set up?"
    return self._racks

  async def set_racks(self, racks: List[PlateCarrier]):
    self._racks = racks

  @abstractmethod
  async def open_door(self):
    pass

  @abstractmethod
  async def close_door(self):
    pass

  @abstractmethod
  async def fetch_plate_to_loading_tray(self, plate: Plate):
    pass

  @abstractmethod
  async def take_in_plate(self, plate: Plate, site: PlateHolder):
    pass

  @abstractmethod
  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius."""

  @abstractmethod
  async def get_temperature(self) -> float:
    """Get the temperature of the incubator in degrees Celsius."""

  @abstractmethod
  async def start_shaking(self, frequency: float):
    """Start shaking the incubator at the given frequency in Hz."""
    pass

  @abstractmethod
  async def stop_shaking(self):
    pass
