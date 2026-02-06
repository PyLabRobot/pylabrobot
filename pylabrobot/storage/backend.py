from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.resources import Plate, PlateCarrier, PlateHolder
from pylabrobot.resources.barcode import Barcode


class IncubatorBackend(MachineBackend, metaclass=ABCMeta):
  def __init__(self):
    super().__init__()
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

  @abstractmethod
  async def stop_shaking(self):
    pass

  @abstractmethod
  async def get_target_temperature(self) -> float:
    """Get the set value temperature of the incubator in degrees Celsius."""

  @abstractmethod
  async def set_humidity(self, humidity: float):
    """Set operation humidity of the incubator in % RH; e.g. 90.0% RH."""

  @abstractmethod
  async def get_humidity(self) -> float:
    """Get the current humidity of the incubator in % RH; e.g. 90.0% RH."""

  @abstractmethod
  async def get_target_humidity(self) -> float:
    """Get the set value humidity of the incubator in % RH; e.g. 90.0% RH."""

  @abstractmethod
  async def set_co2_level(self, co2_level: float):
    """Set operation CO2 level of the incubator in %; e.g. 5.0%."""

  @abstractmethod
  async def get_co2_level(self) -> float:
    """Get the current CO2 level of the incubator in %; e.g. 5.0%."""

  @abstractmethod
  async def get_target_co2_level(self) -> float:
    """Get the set value CO2 level of the incubator in %; e.g. 5.0%."""

  @abstractmethod
  async def set_n2_level(self, n2_level: float):
    """Set operation N2 level of the incubator in %; e.g. 90.0%."""

  @abstractmethod
  async def get_n2_level(self) -> float:
    """Get the current N2 level of the incubator in %; e.g. 90.0%."""

  @abstractmethod
  async def get_target_n2_level(self) -> float:
    """Get the set value N2 level of the incubator in %; e.g. 90.0%."""

  @abstractmethod
  async def move_position_to_position(
    self, plate: Plate, dest_site: PlateHolder, read_barcode: bool = False
  ):
    """Move plate to another position in the storage unit"""
