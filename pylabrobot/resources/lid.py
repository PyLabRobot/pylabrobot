from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from pylabrobot.resources.resource_holder import get_child_location

from .errors import NoLocationError
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
    metadata: Optional[Mapping[str, Any]] = None,
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
      metadata=metadata,
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

  def get_occupied_z_bounds(self) -> Tuple[float, float]:
    """Return the lowest and highest Z of the body and its attached lid, in mm.

    Both values are along this resource's local Z axis and relative to its origin, the bottom of the
    body. A resource placed by its origin therefore occupies ``bottom`` to ``top`` relative to that
    placement. The lid's actual location and rotation relative to
    this resource are used; nesting is reflected in its seated location. Other children, including
    children of the lid, are excluded. This resource's location and rotation, and those of its
    ancestors, do not affect the result. Geometry is read on each call.

    Returns:
      ``(bottom, top)``. Without a lid this is ``(0, get_size_z())``. ``bottom`` is negative when
      the lid extends below the body's origin, and ``top`` exceeds ``get_size_z()`` when the lid
      rises above the body.

    Raises:
      NoLocationError: If the attached lid has no location.
    """
    lid = self.lid
    if lid is None:
      return 0.0, self.get_size_z()
    if lid.location is None:
      raise NoLocationError(f"Lid '{lid.name}' has no location.")
    lid_zs = [
      (lid.location + lid.get_anchor(x=x, y=y, z=z).rotated(lid.rotation)).z
      for x in ("l", "r")
      for y in ("f", "b")
      for z in ("b", "t")
    ]
    return min(0.0, *lid_zs), max(self.get_size_z(), *lid_zs)

  def get_occupied_size_z(self) -> float:
    """Return the total extent of the body and its attached lid along the local Z axis, in mm.

    This is ``top - bottom`` of :meth:`get_occupied_z_bounds`. It is not a height above the body's
    origin: when the lid extends below that origin, the extent exceeds ``top``. It is not a stacking
    pitch either.

    Raises:
      NoLocationError: If the attached lid has no location.
    """
    bottom, top = self.get_occupied_z_bounds()
    return top - bottom

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
