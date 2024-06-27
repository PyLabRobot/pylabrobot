import math
from typing import Optional, cast

from pylabrobot.machines.machine import Machine
from pylabrobot.resources import Coordinate, Plate, Resource

from .tilter_backend import TilterBackend


class Tilter(Machine):
  """ A tilt module """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: TilterBackend,
    category: str = "tilter",
    model: Optional[str] = None
  ):
    super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, backend=backend,
                     category=category, model=model)
    self.backend: TilterBackend = backend  # fix type
    self._angle: int = 0

  def get_plate(self) -> Plate:
    """ Get the plate that is currently attached to the tilt module. If no plate is assigned, raise
    a RuntimeError. """

    if len(self.children) != 1:
      raise RuntimeError("No plate on this tilt module.")

    return cast(Plate, self.children[0])

  def assign_child_resource(self, resource: Resource, location: Coordinate, reassign: bool = True):
    if len(self.children) > 0:
      raise RuntimeError("Tilt module already has a plate.")
    if not isinstance(resource, Plate):
      raise RuntimeError("Tilt module can only have plates.")
    return super().assign_child_resource(resource=resource, location=location, reassign=reassign)

  @property
  def angle(self) -> int:
    return self._angle

  async def set_angle(self, angle: int):
    """ Set the tilt module to rotate by a given angle.

    We assume the rotation anchor is the right side of the module. This may change in the future
    if we integrate other tilt modules.

    Args:
      angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """

    await self.backend.set_angle(angle=angle)

    for well in self.get_plate().children:
      assert well.location is not None

      # Convert angle to radians.
      theta = math.radians(angle)

      # Compute the current location of the well. Use the rotation anchor as the origin.
      x = self.get_plate().get_size_x() - well.location.x
      z = well.location.z
      h = math.sqrt(x**2 + z**2) # hypotenuse (dist from anchor to well)

      d = 1 # offset of well from tilting plane (perpendicular to tilt axis)

      # Compute the new location of the well after rotation.
      x_prime = h * math.cos(theta) + d * math.cos(theta+(math.pi/2)) # x component of vector
      z_prime = h * math.sin(theta) + d * math.sin(theta+(math.pi/2)) # z component of vector

      well.location.x = self.get_plate().get_size_x() - x_prime
      well.location.z = z_prime

    self._angle = angle

  async def tilt(self, angle: int):
    """ Tilt the plate contained in the tilt module by a given angle relative to the current angle.

    Args:
      angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """

    await self.set_angle(self.angle + angle)
