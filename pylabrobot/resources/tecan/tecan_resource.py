from typing import Optional

from pylabrobot.resources.resource import Resource


class TecanResource(Resource):
  """Base class for Tecan deck resources.

  Args:
    name: The name of the resource.
    size_x: The size of the resource in the x-direction.
    size_y: The size of the resource in the y-direction.
    size_z: The size of the resource in the z-direction.
    off_x: Offset in x-direction relative to rails
    off_y: Offset in y-direction relative to rails
    category: The category of the resource, e.g. `tips`, `plate_carrier`, etc.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    off_x: float = 0,
    off_y: float = 0,
    category: Optional[str] = None,
    model: Optional[str] = None,
    preferred_pickup_distance_from_top: Optional[float] = None,
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
      preferred_pickup_distance_from_top=preferred_pickup_distance_from_top,
    )

    self.off_x = off_x
    self.off_y = off_y
