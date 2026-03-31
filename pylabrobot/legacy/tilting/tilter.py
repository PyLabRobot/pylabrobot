"""Legacy. Use a vendor-specific machine class (e.g. HamiltonTiltModule) instead."""

import math
from typing import List, Optional

from pylabrobot.capabilities.tilting import TilterBackend as _NewTilterBackend
from pylabrobot.capabilities.tilting import Tilter
from pylabrobot.legacy.machines import Machine
from pylabrobot.legacy.tilting.tilter_backend import TilterBackend
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.well import CrossSectionType, Well


class _TiltingAdapter(_NewTilterBackend):
  def __init__(self, legacy: TilterBackend):
    self._legacy = legacy

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def set_angle(self, angle: float):
    await self._legacy.set_angle(angle)


class Tilter(ResourceHolder, Machine):
  """Legacy tilt module machine. In new code, use the vendor-specific machine class."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: TilterBackend,
    hinge_coordinate: Coordinate,
    child_location: Coordinate,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      child_location=child_location,
    )
    Machine.__init__(self, backend=backend)
    self.backend: TilterBackend = backend
    self._hinge_coordinate = hinge_coordinate

    self.tilting = Tilter(backend=_TiltingAdapter(backend))
    self._capabilities = [self.tilting]

  @property
  def absolute_angle(self) -> float:
    return self.tilting.absolute_angle

  @property
  def hinge_coordinate(self) -> Coordinate:
    return self._hinge_coordinate

  async def set_angle(self, absolute_angle: float):
    await self.tilting.set_angle(absolute_angle)

  async def tilt(self, relative_angle: float):
    await self.tilting.tilt(relative_angle)

  def experimental_rotate_coordinate_around_hinge(
    self, absolute_coordinate: Coordinate, angle: float
  ) -> Coordinate:
    theta = math.radians(angle)
    origin = self.get_absolute_location("l", "f", "b")

    rotation_arm_x = absolute_coordinate.x - (self._hinge_coordinate.x + origin.x)
    rotation_arm_z = absolute_coordinate.z - (self._hinge_coordinate.z + origin.z)

    x_prime = rotation_arm_x * math.cos(theta) - rotation_arm_z * math.sin(theta)
    z_prime = rotation_arm_x * math.sin(theta) + rotation_arm_z * math.cos(theta)

    new_x = x_prime + (self._hinge_coordinate.x + origin.x)
    new_z = z_prime + (self._hinge_coordinate.z + origin.z)

    return Coordinate(new_x, absolute_coordinate.y, new_z)

  def experimental_get_plate_drain_offsets(
    self, plate: Plate, absolute_angle: Optional[float] = None
  ) -> List[Coordinate]:
    if absolute_angle is None:
      absolute_angle = self.tilting.absolute_angle
    angle = absolute_angle if self._hinge_coordinate.x < self._size_x / 2 else -absolute_angle
    hinge_side = "l" if self._hinge_coordinate.x < self._size_x / 2 else "r"

    well_drain_offsets = []
    for well in plate.children:
      level_coord = well.get_absolute_location(hinge_side, "c", "b")
      rotated_coord = self.experimental_rotate_coordinate_around_hinge(level_coord, angle)
      offset = rotated_coord - well.get_absolute_location("c", "c", "b")
      well_drain_offsets.append(offset)

    return well_drain_offsets

  def experimental_get_well_drain_offsets(
    self,
    wells: List[Well],
    n_tips: int = 1,
    absolute_angle: Optional[float] = None,
  ) -> List[Coordinate]:
    if absolute_angle is None:
      absolute_angle = self.tilting.absolute_angle
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
        rotated_tip = self.experimental_rotate_coordinate_around_hinge(
          well.get_absolute_location("c", "c", "b") + tip_coord,
          angle,
        )
        offset = rotated_tip - well.get_absolute_location("c", "c", "b")
        offsets.append(offset)

      well_drain_offsets.append(offsets)

    return [offset for well_offsets in well_drain_offsets for offset in well_offsets]
