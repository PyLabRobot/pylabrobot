"""BioTek EL406 plate washer device."""

from typing import Optional

from pylabrobot.capabilities.bulk_dispensers.peristaltic import PeristalticDispensing
from pylabrobot.capabilities.bulk_dispensers.syringe import SyringeDispensing
from pylabrobot.capabilities.plate_washing import PlateWashingCapability
from pylabrobot.capabilities.shaking import Shaker
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource

from .driver import EL406Driver
from .peristaltic_dispensing_backend import EL406PeristalticDispensingBackend
from .plate_washing_backend import EL406PlateWashingBackend
from .shaking_backend import EL406ShakingBackend
from .syringe_dispensing_backend import EL406SyringeDispensingBackend


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

    self.washer = PlateWashingCapability(backend=EL406PlateWashingBackend(driver))
    self.shaker = Shaker(backend=EL406ShakingBackend(driver))
    self.syringe = SyringeDispensing(backend=EL406SyringeDispensingBackend(driver))
    self.peristaltic = PeristalticDispensing(backend=EL406PeristalticDispensingBackend(driver))
    self._capabilities = [self.washer, self.shaker, self.syringe, self.peristaltic]

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
      self.syringe.plate = resource
      self.peristaltic.plate = resource

  def _on_plate_unassigned(self, resource: Resource) -> None:
    if isinstance(resource, Plate):
      self.driver._cached_plate = None
      self.washer.plate = None
      self.syringe.plate = None
      self.peristaltic.plate = None

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}
