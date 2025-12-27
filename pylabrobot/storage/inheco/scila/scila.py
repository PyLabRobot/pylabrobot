from typing import Optional

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Resource, ResourceHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.storage.inheco.scila.scila_backend import SCILABackend


class SCILA(Resource, Machine):
  def __init__(
    self,
    scila_ip: str,
    name: str,
    client_ip: Optional[str] = None,
    rotation: Optional[Rotation] = None,
    barcode: Optional[Barcode] = None,
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=250,  # from spec
      size_y=410,  # from spec; drawers closed
      size_z=291,  # from spec
      rotation=rotation,
      category="Storage",
      model="SCILA",
      barcode=barcode,
    )

    Machine.__init__(self, backend=SCILABackend(client_ip=client_ip, scila_ip=scila_ip))
    self.backend: SCILABackend

    drawer_prototype = ResourceHolder(
      "drawer_prototype",
      size_x=222.7,  # from spec
      size_y=234,  # from spec
      size_z=47,  # from spec
      child_location=Coordinate(
        x=222.7 / 2 - (85.6 / 2) - 9.3,  # from spec (assuming SBS plate size)
        y=234 - (44.5 + 127.76),  # from spec
        z=0,
      ),
    )

    self._drawers = {
      1: drawer_prototype.named(f"{self.name}_drawer_1"),
      2: drawer_prototype.named(f"{self.name}_drawer_2"),
      3: drawer_prototype.named(f"{self.name}_drawer_3"),
      4: drawer_prototype.named(f"{self.name}_drawer_4"),
    }

    for i, drawer in enumerate(self._drawers.values()):
      self.assign_child_resource(
        drawer,
        location=Coordinate(
          x=125 - 222.7 / 2,  # from spec
          y=-234,  # from spec
          z=71.2 + i * 47,  # from spec
        ),
      )

  @property
  def drawers(self) -> dict[int, ResourceHolder]:
    """The drawers of the SCILA."""
    return self._drawers
