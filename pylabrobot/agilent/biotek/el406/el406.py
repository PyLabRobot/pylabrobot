"""BioTek EL406 plate washer device."""

from typing import Optional

from pylabrobot.capabilities.bulk_dispensers.peristaltic import PeristalticDispensing8
from pylabrobot.capabilities.bulk_dispensers.syringe import SyringeDispensing8
from pylabrobot.capabilities.plate_washing import PlateWasher96
from pylabrobot.capabilities.shaking import Shaker
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource

from .driver import EL406Driver
from .peristaltic_dispensing_backend8 import EL406PeristalticDispensingBackend8
from .plate_washing_backend import EL406PlateWasher96Backend
from .shaking_backend import EL406ShakingBackend
from .syringe_dispensing_backend8 import EL406SyringeDispensingBackend8


class EL406(Resource, Device):
  """BioTek EL406 plate washer.

  Example:
    >>> el406 = EL406(name="el406")
    >>> await el406.setup()
    >>> await el406.washer.wash(plate, cycles=3)
    >>> await el406.stop()
  """

  def __init__(
    self,
    name: str,
    device_id: Optional[str] = None,
    timeout: float = 15.0,
    size_x: float = 0.0,
    size_y: float = 0.0,
    size_z: float = 0.0,
  ):
    driver = EL406Driver(timeout=timeout, device_id=device_id)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="BioTek EL406",
    )
    Device.__init__(self, driver=driver)
    self.driver: EL406Driver = driver

    self.washer = PlateWasher96(backend=EL406PlateWasher96Backend(driver))
    self.shaker = Shaker(backend=EL406ShakingBackend(driver))
    self.syringe_dispenser = SyringeDispensing8(backend=EL406SyringeDispensingBackend8(driver))
    self.peristaltic_dispenser = PeristalticDispensing8(backend=EL406PeristalticDispensingBackend8(driver))
    self._capabilities = [self.washer, self.shaker, self.syringe_dispenser, self.peristaltic_dispenser]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())
    self.plate_holder.register_did_assign_resource_callback(self._on_plate_assigned)
    self.plate_holder.register_did_unassign_resource_callback(self._on_plate_unassigned)

  def _on_plate_assigned(self, resource: Resource) -> None:
    if isinstance(resource, Plate):
      self.driver._cached_plate = resource
      self.washer.plate = resource
      self.syringe_dispenser.plate = resource
      self.peristaltic_dispenser.plate = resource

  def _on_plate_unassigned(self, resource: Resource) -> None:
    if isinstance(resource, Plate):
      self.driver._cached_plate = None
      self.washer.plate = None
      self.syringe_dispenser.plate = None
      self.peristaltic_dispenser.plate = None

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}
