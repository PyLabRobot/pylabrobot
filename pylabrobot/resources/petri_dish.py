from typing import Callable, Optional, cast

from .container import Container
from .coordinate import Coordinate
from .resource import Resource


class PetriDish(Container):
  """ A petri dish """

  def __init__(
    self,
    name: str,
    diameter: float,
    height: float,
    material_z_thickness: Optional[float] = None,
    category: str = "petri_dish",
    model: Optional[str] = None,
    max_volume: Optional[float] = None,
    compute_volume_from_height: Optional[Callable[[float], float]] = None,
    compute_height_from_volume: Optional[Callable[[float], float]] = None,
  ):
    super().__init__(
      name=name,
      size_x=diameter,
      size_y=diameter,
      size_z=height,
      material_z_thickness=material_z_thickness,
      category=category,
      model=model,
      max_volume=max_volume,
      compute_volume_from_height=compute_volume_from_height,
      compute_height_from_volume=compute_height_from_volume,
    )
    self.diameter = diameter
    self.height = height

  def serialize(self):
    super_serialized = super().serialize()
    for key in ["size_x", "size_y", "size_z"]:
      del super_serialized[key]

    return {
      **super_serialized,
      "diameter": self.diameter,
      "height": self.height
    }


class PetriDishHolder(Resource):
  """ 3d printed holder for petri dish, size of a 96 well plate """

  def __init__(
    self,
    name: str,
    size_x: float = 127.76,
    size_y: float = 85.48,
    size_z: float = 14.5,
    category: str = "petri_dish_holder",
    model: Optional[str] = None
  ):
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )

  def assign_child_resource(self, resource: Resource, location: Coordinate, reassign: bool = True):
    """ Can only assign a single PetriDish """
    if not isinstance(resource, PetriDish):
      raise TypeError("Can only assign PetriDish to PetriDishHolder")

    if len(self.children) > 0:
      raise ValueError("Can only assign a single PetriDish to PetriDishHolder")

    super().assign_child_resource(resource, location, reassign)

  @property
  def dish(self) -> Optional[PetriDish]:
    if len(self.children) == 0:
      return None
    return cast(PetriDish, self.children[0])
