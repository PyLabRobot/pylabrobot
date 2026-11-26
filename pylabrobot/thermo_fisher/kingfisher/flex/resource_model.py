"""Logical Flex tray resources, without calibrated physical geometry."""

from typing import Optional

from pylabrobot.resources import Coordinate, Plate, PlateCarrier, PlateHolder, Resource


class FlexPlateHolder(PlateHolder):
  """A Flex tray accepting one plate, without stacks or implicit replacement."""

  def _check_assignment(self, resource: Resource, reassign: bool = True) -> None:
    """Enforce tray occupancy for direct assignments and transfer preflight alike."""
    if not isinstance(resource, Plate):
      raise TypeError("A Flex tray can hold only one Plate")
    if self.resource is not None and self.resource is not resource:
      raise ValueError("Flex tray is occupied; remove its plate before assigning another")
    super()._check_assignment(resource, reassign=reassign)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ) -> None:
    """Assign a plate without applying unmeasured pedestal or skirt geometry."""
    Resource.assign_child_resource(self, resource, location or Coordinate.zero(), reassign)

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Apply the same capacity and plate checks before a proposed assignment."""
    self._check_assignment(resource, reassign=reassign)


def create_carousel(name: str) -> PlateCarrier:
  """Create eight persistent, one-based trays with undefined carousel positions.

  Zero extents and pedestal heights are unmeasured placeholders, not physical
  dimensions. Child plate coordinates are local bookkeeping only. This resource
  tree is not calibrated for robotic transfers or collision checking.
  """
  carousel = PlateCarrier(
    name=f"{name}_carousel",
    size_x=0,
    size_y=0,
    size_z=0,
    model="KingFisher Flex carousel",
    metadata={"geometry": "unmeasured"},
  )
  for tray in range(1, 9):
    holder = FlexPlateHolder(
      name=f"{name}_tray_{tray}",
      size_x=0,
      size_y=0,
      size_z=0,
      pedestal_size_z=0,
      model="KingFisher Flex tray",
      metadata={"geometry": "unmeasured", "tray_number": tray},
    )
    carousel.assign_child_resource(holder, location=None, spot=tray)
  return carousel
