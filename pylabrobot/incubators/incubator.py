from typing import Dict, Optional

from pylabrobot.machines import Machine
from pylabrobot.resources import Plate, Resource, ResourceHolder, ResourceNotFoundError, Rotation

from .backend import IncubatorBackend


class Incubator(Machine, Resource):
  def __init__(
    self,
    backend: IncubatorBackend,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    rotation: Optional[Rotation] = None,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Machine.__init__(self, backend=backend)
    self.backend: IncubatorBackend = backend  # fix type
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      rotation=rotation,
      category=category,
      model=model,
    )
    self.loading_tray = ResourceHolder(
      name=self.name + "_tray", size_x=127.76, size_y=85.48, size_z=0
    )

    # self.plates: Dict[str, Plate] = {}

    # backend: plate name, incubator idx
    # front end: plate name, plate. can be in `children` i think

  async def fetch_plate(self, plate_name: str):
    """Fetch a plate from the incubator and put it on the loading tray."""

    if plate_name not in self.plates:
      raise ResourceNotFoundError(f"Plate {plate_name} not found in incubator '{self.name}'")

    plate = self.plates[plate_name]
    self.loading_tray.assign_child_resource(plate)
    # del self.plates[plate_name]

  async def take_in_plate(self):
    """Take a plate from the loading tray and put it in the incubator."""

    plate = self.loading_tray.child_resource
    if plate is None:
      raise ResourceNotFoundError(f"No plate on the loading tray of incubator '{self.name}'")

    await self.backend.take_in_plate(plate)

    self.plates[plate.name] = plate
    plate.unassign()

  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius."""
    return await self.backend.set_temperature(temperature)

  async def get_temperature(self) -> float:
    return await self.backend.get_temperature()
