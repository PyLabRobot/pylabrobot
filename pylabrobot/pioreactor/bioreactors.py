from typing import Optional

from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource


class Pioreactor(Resource):
  """A Pioreactor bioreactor unit (https://pioreactor.com).

  The culture vessel is modeled as a single child :class:`Container`.
  """

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True,
  ):
    if not isinstance(resource, Container):
      raise TypeError("Pioreactor can only hold a Container as a child.")
    if len(self.children) > 0:
      raise ValueError("Pioreactor already has a vessel assigned.")
    super().assign_child_resource(resource, location, reassign=reassign)

  @property
  def vessel(self) -> Container:
    if not self.children:
      raise ValueError(f"Pioreactor '{self.name}' has no vessel assigned.")
    vessel = self.children[0]
    assert isinstance(vessel, Container)
    return vessel


def pioreactor_20ml(name: str) -> Pioreactor:
  """Pioreactor 20mL Vessel: https://pioreactor.com/products/pioreactor-20ml

  Geometry (mm):
  - Outer footprint (on holder): 127.74 x 85.40
  - Total height: 126.5
  - Vessel cavity: Ø 23.5, depth 57.0, centered
  """
  outer_x = 127.74
  outer_y = 85.40
  outer_z = 126.5
  diameter = 23.5
  depth = 57.0
  material_z_thickness = 1.0
  vessel_z = 76.0  # measured: vessel outer base relative to pioreactor bottom

  pioreactor = Pioreactor(
    name=name,
    size_x=outer_x,
    size_y=outer_y,
    size_z=outer_z,
    model=pioreactor_20ml.__name__,
    category="bioreactor",
  )

  vessel = Container(
    name=f"{name}_vessel",
    size_x=diameter,
    size_y=diameter,
    size_z=depth,
    material_z_thickness=material_z_thickness,
    max_volume=20_000,
    category="bioreactor_vessel",
  )
  pioreactor.assign_child_resource(
    vessel,
    location=Coordinate(
      x=(outer_x - diameter) / 2.0,
      y=(outer_y - diameter) / 2.0,
      z=vessel_z,
    ),
  )
  return pioreactor
