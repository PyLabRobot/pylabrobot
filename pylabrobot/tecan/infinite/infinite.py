"""Tecan Infinite 200 PRO plate reader device."""

from __future__ import annotations

from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.carrier import PlateHolder

from .absorbance_backend import TecanInfiniteAbsorbanceBackend
from .driver import TecanInfiniteDriver
from .fluorescence_backend import TecanInfiniteFluorescenceBackend
from .luminescence_backend import TecanInfiniteLuminescenceBackend


class TecanInfinite200Pro(Resource, Device):
  """Tecan Infinite 200 PRO plate reader.

  Supports absorbance, fluorescence, and luminescence measurements.

  Examples:
    >>> reader = TecanInfinite200Pro(name="infinite")
    >>> await reader.setup()
    >>> results = await reader.absorbance.read(plate=my_plate, wavelength=600)
    >>> await reader.stop()
  """

  def __init__(
    self,
    name: str,
    counts_per_mm_x: float = 1_000,
    counts_per_mm_y: float = 1_000,
    counts_per_mm_z: float = 1_000,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    driver = TecanInfiniteDriver(
      counts_per_mm_x=counts_per_mm_x,
      counts_per_mm_y=counts_per_mm_y,
      counts_per_mm_z=counts_per_mm_z,
    )
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Tecan Infinite 200 PRO",
      category="plate_reader",
    )
    Device.__init__(self, driver=driver)
    self.driver: TecanInfiniteDriver = driver

    self.absorbance = Absorbance(backend=TecanInfiniteAbsorbanceBackend(driver))
    self.fluorescence = Fluorescence(backend=TecanInfiniteFluorescenceBackend(driver))
    self.luminescence = Luminescence(backend=TecanInfiniteLuminescenceBackend(driver))
    self._capabilities = [self.absorbance, self.fluorescence, self.luminescence]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self) -> None:
    """Open the plate tray."""
    await self.driver.open_tray()

  async def close(self) -> None:
    """Close the plate tray."""
    await self.driver.close_tray()
