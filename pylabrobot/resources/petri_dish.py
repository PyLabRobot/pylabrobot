from typing import Optional, cast

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
    category: str = "petri_dish",
    model: Optional[str] = None
  ):
    super().__init__(
      name=name,
      size_x=diameter,
      size_y=diameter,
      size_z=height,
      category=category,
      model=model,
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
    size_x: float = 127.0,
    size_y: float = 86.0,
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

  def assign_child_resource(
      self,
      resource: Resource,
      location: Optional[Coordinate],
      reassign: bool = True):
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
