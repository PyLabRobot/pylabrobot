from __future__ import annotations

from typing import Optional

from pylabrobot.resources.resource_holder import get_child_location

from .resource import Coordinate, Resource

# A lid may be modelled up to this much smaller than the resource it covers - its rim sits just
# inside the outer edge, so real plate lids run a few tenths of a mm under. A larger shortfall means
# the wrong lid. Used by Liddable.assign_child_resource.
LID_UNDERSIZE_TOLERANCE = 1.0


class Lid(Resource):
  """A removable cover seated on top of a :class:`Liddable` resource.

  Any liddable resource - a ``Plate`` or a ``Container`` (trough, tube, petri dish, well) - can carry
  one. A lid is a standalone resource, moved with the gripper (``LiquidHandler.move_lid``) or
  assigned as a child; when seated it is centred on the parent's top face and lowered by
  ``nesting_z_height``, the vertical overlap between the lid and the parent it rests on.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    nesting_z_height: float,
    category: str = "lid",
    model: Optional[str] = None,
  ):
    """Create a lid.

    Args:
      name: Name of the lid.
      size_x: Size of the lid in x-direction.
      size_y: Size of the lid in y-direction.
      size_z: Size of the lid in z-direction.
      nesting_z_height: the overlap in mm between the lid and its parent (in the z-direction).
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    self.nesting_z_height = nesting_z_height
    if nesting_z_height == 0:
      print(f"{self.name}: Are you certain that the lid nests 0 mm with its parent?")

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "nesting_z_height": self.nesting_z_height,
    }


class Liddable(Resource):
  """Mixin: a resource that can host a single :class:`Lid` on its top face.

  Mixed into :class:`~pylabrobot.resources.plate.Plate` and
  :class:`~pylabrobot.resources.container.Container` (so troughs, tubes, petri dishes, and wells
  can carry a lid). The lid is seated centred on the parent's top face and sunk by its own
  ``nesting_z_height``; a resource may hold at most one lid at a time.
  """

  def has_lid(self) -> bool:
    return self.lid is not None

  @property
  def lid(self) -> Optional[Lid]:
    """The lid seated on this resource, or ``None``. Derived from the children."""
    return next((child for child in self.children if isinstance(child, Lid)), None)

  @lid.setter
  def lid(self, lid: Optional[Lid]) -> None:
    if lid is None:
      current_lid = self.lid
      if current_lid is not None:
        self.unassign_child_resource(current_lid)
    else:
      self.assign_child_resource(lid)

  def get_lid_location(self, lid: Lid) -> Coordinate:
    """Location of ``lid`` seated centred on this resource's top face, sunk by nesting_z_height.

    Centres the lid on the footprint - a no-op when the lid shares the footprint (e.g. plate lids),
    so this is backwards-compatible - and drops its origin to ``size_z - nesting_z_height``.
    ``get_child_location`` keeps a rotated lid's footprint aligned.
    """
    return (
      get_child_location(lid)
      + self.get_anchor(x="c", y="c", z="t")
      - lid.get_anchor(x="c", y="c", z="b")
      - Coordinate(0, 0, lid.nesting_z_height)
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if isinstance(resource, Lid):
      if self.has_lid():
        raise ValueError(f"'{self.name}' already has a lid.")
      if (
        resource.get_size_x() < self.get_size_x() - LID_UNDERSIZE_TOLERANCE
        or resource.get_size_y() < self.get_size_y() - LID_UNDERSIZE_TOLERANCE
      ):
        raise ValueError(
          f"Lid '{resource.name}' ({resource.get_size_x()} x {resource.get_size_y()} mm) is smaller "
          f"than '{self.name}' ({self.get_size_x()} x {self.get_size_y()} mm) and cannot cover it."
        )
      location = location or self.get_lid_location(resource)
    return super().assign_child_resource(resource, location=location, reassign=reassign)
