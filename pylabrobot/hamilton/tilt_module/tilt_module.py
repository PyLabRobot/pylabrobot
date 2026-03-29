import math
from typing import List, Optional

from pylabrobot.capabilities.tilting import Tilter
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.well import CrossSectionType, Well

from .backend import HamiltonTiltModuleDriver, HamiltonTiltModuleTilterBackend


class HamiltonTiltModule(ResourceHolder, Device):
  """A Hamilton tilt module."""

  def __init__(
    self,
    name: str,
    com_port: str,
    child_location: Coordinate = Coordinate(1.0, 3.0, 83.55),
    pedestal_size_z: float = 3.47,
    write_timeout: float = 3,
    timeout: float = 3,
  ):
    driver = HamiltonTiltModuleDriver(
      com_port=com_port,
      write_timeout=write_timeout,
      timeout=timeout,
    )
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=132,
      size_y=92.57,
      size_z=85.81,
      child_location=child_location,
      category="tilter",
      model="HamiltonTiltModule",
    )
    Device.__init__(self, driver=driver)
    self._driver: HamiltonTiltModuleDriver = driver
    self.pedestal_size_z = pedestal_size_z
    self._hinge_coordinate = Coordinate(6.18, 0, 72.85)

    self.tilter = Tilter(backend=HamiltonTiltModuleTilterBackend(driver=driver))
    self._capabilities = [self.tilter]

  @property
  def hinge_coordinate(self) -> Coordinate:
    return self._hinge_coordinate

  def rotate_coordinate_around_hinge(
    self, absolute_coordinate: Coordinate, angle: float
  ) -> Coordinate:
    """Rotate an absolute coordinate around the hinge by a given angle.

    Args:
      absolute_coordinate: The coordinate to rotate.
      angle: The angle to rotate by, in degrees. Negative is clockwise.
    """
    theta = math.radians(angle)
    origin = self.get_absolute_location("l", "f", "b")

    rotation_arm_x = absolute_coordinate.x - (self._hinge_coordinate.x + origin.x)
    rotation_arm_z = absolute_coordinate.z - (self._hinge_coordinate.z + origin.z)

    x_prime = rotation_arm_x * math.cos(theta) - rotation_arm_z * math.sin(theta)
    z_prime = rotation_arm_x * math.sin(theta) + rotation_arm_z * math.cos(theta)

    new_x = x_prime + (self._hinge_coordinate.x + origin.x)
    new_z = z_prime + (self._hinge_coordinate.z + origin.z)

    return Coordinate(new_x, absolute_coordinate.y, new_z)

  def get_plate_drain_offsets(
    self, plate: Plate, absolute_angle: Optional[float] = None
  ) -> List[Coordinate]:
    """Get drain edge offsets for all wells in the plate at the given tilt angle.

    Args:
      plate: The plate to calculate the offsets for.
      absolute_angle: The absolute angle. If None, uses the current tilt angle.
    """
    if absolute_angle is None:
      absolute_angle = self.tilter.absolute_angle
    angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle
    hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

    well_drain_offsets = []
    for well in plate.children:
      level_coord = well.get_absolute_location(hinge_side, "c", "b")
      rotated_coord = self.rotate_coordinate_around_hinge(level_coord, angle)
      offset = rotated_coord - well.get_absolute_location("c", "c", "b")
      well_drain_offsets.append(offset)

    return well_drain_offsets

  def get_well_drain_offsets(
    self,
    wells: List[Well],
    n_tips: int = 1,
    absolute_angle: Optional[float] = None,
  ) -> List[Coordinate]:
    """Get drain edge offsets for the given wells at the given tilt angle.

    Args:
      wells: The wells to calculate the offsets for.
      n_tips: The number of tips per well. Defaults to 1.
      absolute_angle: The absolute angle. If None, uses the current tilt angle.
    """
    if absolute_angle is None:
      absolute_angle = self.tilter.absolute_angle
    angle = absolute_angle * (-1 if self._hinge_coordinate.x >= self._size_x / 2 else 1)

    hinge_on_left = self._hinge_coordinate.x < self._size_x / 2
    min_tip_distance = 9  # mm

    well_drain_offsets = []
    for well in wells:
      assert well.cross_section_type == CrossSectionType.CIRCLE, (
        "Wells must have circular cross-section"
      )

      diameter = well.get_absolute_size_x()
      radius = diameter / 2

      if n_tips > 1:
        assert (n_tips - 1) * min_tip_distance <= diameter, (
          f"Cannot fit {n_tips} tips in a well with diameter {diameter} mm"
        )
        y_offsets = [
          ((n_tips - 1) / 2 - tip_index) * min_tip_distance for tip_index in range(n_tips)
        ]
        x_offset = math.sqrt(radius**2 - max(y_offsets) ** 2)
        x_offset = -x_offset if hinge_on_left else x_offset
        tip_coords = [Coordinate(x_offset, y, 0) for y in y_offsets]
      else:
        x_offset = -radius if hinge_on_left else radius
        tip_coords = [Coordinate(x_offset, 0, 0)]

      offsets = []
      for tip_coord in tip_coords:
        rotated_tip = self.rotate_coordinate_around_hinge(
          well.get_absolute_location("c", "c", "b") + tip_coord,
          angle,
        )
        offset = rotated_tip - well.get_absolute_location("c", "c", "b")
        offsets.append(offset)

      well_drain_offsets.append(offsets)

    return [offset for well_offsets in well_drain_offsets for offset in well_offsets]
