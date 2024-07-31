import math
from typing import List, Optional

from pylabrobot.machines import Machine
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.well import Well

from .tilter_backend import TilterBackend


class Tilter(Machine):
  """ Resources that tilt plates. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: TilterBackend,
    hinge_coordinate: Coordinate,
    child_resource_location: Coordinate,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x, size_y=size_y, size_z=size_z, backend=backend,
      category=category, model=model)
    self.backend: TilterBackend = backend  # fix type
    self._absolute_angle: float = 0
    self._hinge_coordinate = hinge_coordinate
    self.child_resource_location = child_resource_location

  @property
  def absolute_angle(self) -> float:
    return self._absolute_angle

  async def set_angle(self, absolute_angle: float):
    """ Set the tilt module to rotate to a given angle.

    Args:
      absolute_angle: The absolute (unsigned) angle to set rotation to, in degrees, measured from
        horizontal as zero.
    """

    await self.backend.set_angle(angle=absolute_angle)
    self._absolute_angle = absolute_angle

  def rotate_coordinate_around_hinge(self, absolute_coordinate: Coordinate, angle: float) \
    -> Coordinate:
    """ Rotate an absolute coordinate around the hinge of the tilter by a given angle.

    Args:
      absolute_coordinate: The coordinate to rotate.
      angle: The angle to rotate by, in degrees. Negative is clockwise.

    Returns:
      Coordinate: The new coordinate after rotation.
    """
    theta = math.radians(angle)

    rotation_arm_x = absolute_coordinate.x - (
      self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x
    )
    rotation_arm_z = absolute_coordinate.z - (
      self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z
    )

    x_prime = rotation_arm_x * math.cos(theta) - rotation_arm_z * math.sin(theta)
    z_prime = rotation_arm_x * math.sin(theta) + rotation_arm_z * math.cos(theta)

    new_x = x_prime + (self._hinge_coordinate.x + self.get_absolute_location("l", "f", "b").x)
    new_z = z_prime + (self._hinge_coordinate.z + self.get_absolute_location("l", "f", "b").z)

    return Coordinate(new_x, absolute_coordinate.y, new_z)

  def get_plate_drain_offsets(self, plate: Plate, absolute_angle: Optional[float] = None) \
    -> List[Coordinate]:
    """ Get the drain edge offsets for all wells in the given plate, tilted around the hinge at a
    given absolute angle.

    Args:
      plate: The plate to calculate the offsets for.
      absolute_angle: The absolute angle to rotate the plate. If `None`, the current tilt angle.
    """

    if absolute_angle is None:
      absolute_angle = self._absolute_angle
    assert absolute_angle is not None # mypy
    angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle

    _hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

    well_drain_offsets = []
    for well in plate.children:
      level_absolute_well_drain_coordinate = well.get_absolute_location(_hinge_side, "c", "b")
      rotated_absolute_well_drain_coordinate = self.rotate_coordinate_around_hinge(
        level_absolute_well_drain_coordinate, angle
      )
      well_drain_offset = (rotated_absolute_well_drain_coordinate -
                           well.get_absolute_location("c", "c", "b"))
      well_drain_offsets.append(well_drain_offset)

    return well_drain_offsets

  def get_well_drain_offsets(self, wells: List[Well], absolute_angle: Optional[float] = None) \
    -> List[Coordinate]:
    """ Get the drain edge offsets for the given wells, tilted around the hinge at a
    given absolute angle.

    Args:
      wells: The wells to calculate the offsets for.
      absolute_angle: The absolute angle to rotate the wells. If `None`, the current tilt angle.
    """

    if absolute_angle is None:
      absolute_angle = self._absolute_angle
    assert absolute_angle is not None # mypy
    angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle

    _hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

    well_drain_offsets = []
    for well in wells:
      level_absolute_well_drain_coordinate = well.get_absolute_location(_hinge_side, "c", "b")
      rotated_absolute_well_drain_coordinate = self.rotate_coordinate_around_hinge(
        level_absolute_well_drain_coordinate, angle
      )
      well_drain_offset = (rotated_absolute_well_drain_coordinate -
                           well.get_absolute_location("c", "c", "b"))
      well_drain_offsets.append(well_drain_offset)

    return well_drain_offsets

  async def tilt(self, relative_angle: float):
    """ Tilt the plate contained in the tilt module by a given angle relative to the current angle.

    Args:
      relative_angle: The angle to rotate by, in degrees. Clockwise. 0 is horizontal.
    """
    await self.set_angle(self._absolute_angle + relative_angle)
