"""PyLabRobot deck resource for the Agilent Bravo's nine deck sites.

Bridges PyLabRobot's own resource-assignment model onto the instrument's
nine taught deck locations, so plates and tip racks a protocol assigns
through PyLabRobot can be translated into the internal
:class:`~.labware.Labware` model the state-machine tasks consume.
"""

from __future__ import annotations

from typing import Any, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.trash import Trash

from ..types import (
  MAX_COLS,
  MAX_LOCATIONS,
  MAX_ROWS,
  MIN_LOCATION,
  X_TO_X_DISTANCE,
  Y_TO_Y_DISTANCE,
  HeadType,
)
from .labware import Labware
from .teachpoints import Teachpoints

_SITE_SIZE_X_MM = 127.76
"""Nominal SLAS plate-footprint width for an empty site holder's own bounding
box. Purely cosmetic for an empty site: once a resource is assigned, its own
footprint is what matters."""

_SITE_SIZE_Y_MM = 85.48
"""Nominal SLAS plate-footprint depth. See :data:`_SITE_SIZE_X_MM`."""

_GRID_MARGIN_X_MM = 130.0
"""Extra width, beyond the taught site-to-site pitch, the deck's own bounding
box extends past the outermost sites. Cosmetic only -- physical motion is
governed entirely by teachpoints and axis travel limits, not this size."""

_GRID_MARGIN_Y_MM = 95.0
"""Extra depth beyond the taught site-to-site pitch. See :data:`_GRID_MARGIN_X_MM`."""


def _default_teachpoints(head_type: HeadType) -> Teachpoints:
  """Build a default set of teachpoints for *head_type*."""
  teachpoints = Teachpoints()
  teachpoints.set_default_teachpoints(head_type)
  return teachpoints


def _labware_kind_for_resource(resource: Resource) -> str:
  """Return the internal labware ``kind``/``base_class`` string for *resource*.

  A :class:`~pylabrobot.resources.trash.Trash` (including subclasses, e.g.
  :class:`~pylabrobot.resources.tecan.trash.TecanTrash`) maps to
  ``"tip_trash"`` so :meth:`~..bravo.Bravo.tips_off`'s own
  ``{"tip_box", "tip_trash"}`` check accepts a site a protocol drops tips
  into -- without this, every :class:`Trash` fell through to the generic
  ``"plate"`` case below and a tip drop to trash always failed with "Tips
  off requires a tip box or tip trash".
  """
  if isinstance(resource, TipRack):
    return "tip_box"
  if isinstance(resource, Trash):
    return "tip_trash"
  return "plate"


def _well_grid_metadata(resource: Resource) -> "dict[str, Any]":
  """Return well-grid metadata for *resource*, if it is a uniform item grid.

  Populated from the resource's own item-grid introspection (row/column
  count, item pitch, and item A1's offset from the resource's own origin)
  when *resource* is an :class:`~pylabrobot.resources.itemized_resource.ItemizedResource`
  (covers both :class:`~pylabrobot.resources.plate.Plate` and
  :class:`~pylabrobot.resources.tip_rack.TipRack`). Empty for any other
  resource type, or one whose item grid cannot be read, which leaves
  well-geometry lookups on that labware falling back to
  :func:`~.geometry.well_geometry_from_metadata`'s own SBS defaults.

  Unvalidated against real hardware, specifically: ``offset_x_mm``/
  ``offset_y_mm`` here are set directly from ``item_a1.location`` --
  PyLabRobot's own offset from the resource's origin corner to well A1's
  center. Combined with this package's own
  :func:`~.geometry.well_center_offset_from_teachpoint_mm` (which treats
  ``offset_x_mm``/``offset_y_mm`` as the teachpoint-to-A1 offset and
  negates it), the resulting well target this translator currently
  produces is::

      A1_target_xy = site_teachpoint_xy - (item_a1.location.x, item_a1.location.y)

  not the sign-flipped alternative (``site_teachpoint_xy + item_a1.location``).
  Which of the two actually matches where the instrument physically expects
  A1 to be has not been confirmed against a real Bravo -- a
  hardware-in-the-loop check (teach a site, place a plate whose A1 corner
  is visually known, compare against this formula's prediction) is needed
  before trusting this for real liquid handling.
  :meth:`resource_tests.LabwareForSiteTests.test_pins_the_well_target_sign_convention_this_translator_assumes`
  pins the formula above exactly, so a hardware check that finds it
  backwards fails that one test and points straight at this docstring.

  A symptom downstream of this same unconfirmed sign, observed while
  testing :class:`~..backend.AgilentBravoBackend` against a real
  :class:`~pylabrobot.resources.plate.Plate` (``cor_96_wellplate_360uL_Fb``)
  under :meth:`~.teachpoints.Teachpoints.set_default_teachpoints`'s default
  teachpoints for a ``96_d_70`` head: of the deck's nine sites, only sites 5
  and 8 (the middle column) had every one of the plate's 96 wells reachable
  as a plate anchor; sites 1, 2, and 3 (the back row) had none reachable at
  all, and sites 4, 6, 7, and 9 had some but not all. This is expected to
  change once the sign convention above is confirmed on a real instrument --
  it is not a separate bug to chase, just a concrete instance of the same
  unconfirmed formula, recorded here rather than left to be rediscovered.
  """
  if not isinstance(resource, ItemizedResource):
    return {}
  try:
    rows = resource.num_items_y
    cols = resource.num_items_x
    item_a1 = resource.get_item("A1")
  except (ValueError, IndexError):
    return {}
  if item_a1.location is None:
    return {}
  metadata: "dict[str, Any]" = {
    "rows": rows,
    "cols": cols,
    "offset_x_mm": float(item_a1.location.x),
    "offset_y_mm": float(item_a1.location.y),
  }
  if cols >= 2:
    metadata["spacing_x_mm"] = abs(float(resource.item_dx))
  if rows >= 2:
    metadata["spacing_y_mm"] = abs(float(resource.item_dy))
  return metadata


def labware_from_resource(resource: Resource) -> Labware:
  """Build an internal :class:`Labware` description from a PyLabRobot resource.

  Draws physical dimensions directly from the resource's own
  ``get_size_x``/``get_size_y``/``get_size_z``, and, for a
  :class:`~pylabrobot.resources.itemized_resource.ItemizedResource` (a
  :class:`~pylabrobot.resources.plate.Plate` or
  :class:`~pylabrobot.resources.tip_rack.TipRack`), well-grid metadata from
  the resource's item layout. See :func:`_well_grid_metadata` for the
  validation caveat on that grid metadata.

  Args:
    resource: The PyLabRobot resource assigned to a Bravo deck site.

  Returns:
    The internal labware description the state-machine tasks consume.
  """
  kind = _labware_kind_for_resource(resource)
  metadata: "dict[str, Any]" = {
    "name": resource.name,
    "kind": kind,
    "base_class": kind,
    "length_mm": resource.get_size_x(),
    "width_mm": resource.get_size_y(),
    "height_mm": resource.get_size_z(),
  }
  metadata.update(_well_grid_metadata(resource))
  return Labware(
    id=resource.name,
    definition_id=resource.name,
    name=resource.name,
    height=resource.get_size_z(),
    width=resource.get_size_y(),
    length=resource.get_size_x(),
    labware_type=kind,
    gripper_offset=0.0,
    stack_height=resource.get_size_z(),
    wells=int(metadata.get("rows", 0)) * int(metadata.get("cols", 0)),
    metadata=metadata,
  )


class BravoDeck(Deck):
  """PyLabRobot deck model for the Agilent Bravo's nine deck sites.

  Models the instrument's fixed 3x3 grid of deck locations as nine
  :class:`~pylabrobot.resources.resource_holder.ResourceHolder` children,
  one per site, positioned directly from the instrument's taught X/Y/Z --
  the same taught positions a :class:`~..bravo.Bravo` facade constructed
  against this deck uses to drive the physical head, so the deck model
  reflects the real machine rather than a nominal layout. Sites are
  numbered 1 through 9 in the row-major order the rest of this package uses
  for deck locations.

  A site's taught position is used as its origin unchanged, in the same
  coordinate frame the teachpoints are already expressed in -- no
  additional transform is applied.
  """

  def __init__(
    self,
    head_type: HeadType = "96_d_70",
    teachpoints: Optional[Teachpoints] = None,
    name: str = "bravo_deck",
    category: str = "deck",
  ) -> None:
    """Initialize the deck and its nine sites.

    Args:
      head_type: The installed head type, used to build a default set of
        taught positions when *teachpoints* is not given.
      teachpoints: The taught positions to place each site's origin at.
        Defaults to a fresh set built for *head_type*. When a
        :class:`~..bravo.Bravo` facade is also constructed for the same
        instrument, pass this same object to both so they agree on where
        every site actually is.
      name: The deck's resource name.
      category: The deck's resource category.
    """
    size_x = (MAX_COLS - 1) * X_TO_X_DISTANCE + _GRID_MARGIN_X_MM
    size_y = (MAX_ROWS - 1) * Y_TO_Y_DISTANCE + _GRID_MARGIN_Y_MM
    super().__init__(size_x=size_x, size_y=size_y, size_z=0.0, name=name, category=category)
    self._teachpoints = teachpoints if teachpoints is not None else _default_teachpoints(head_type)
    self._site_holders: "dict[int, ResourceHolder]" = {}
    for site in range(MIN_LOCATION, MAX_LOCATIONS + 1):
      x = self._teachpoints.get_teachpoint(site, "x")
      y = self._teachpoints.get_teachpoint(site, "y")
      z = self._teachpoints.get_teachpoint(site, "z")
      holder = ResourceHolder(
        name=f"{self.name}_site_{site}",
        size_x=_SITE_SIZE_X_MM,
        size_y=_SITE_SIZE_Y_MM,
        size_z=0.0,
      )
      self._site_holders[site] = holder
      super().assign_child_resource(holder, location=Coordinate(x=x, y=y, z=z))

  @property
  def teachpoints(self) -> Teachpoints:
    """The taught positions each site's origin was placed from."""
    return self._teachpoints

  def _validate_site(self, site: int) -> None:
    """Raise if *site* is not one of the nine deck sites."""
    if site not in self._site_holders:
      raise ValueError(f"Site must be {MIN_LOCATION}-{MAX_LOCATIONS}, got {site}")

  def assign_child_at_site(self, resource: Resource, site: int) -> None:
    """Assign *resource* to a deck site.

    Args:
      resource: The plate, tip rack, or other resource to place.
      site: The deck site to place it at, 1 through 9.

    Raises:
      ValueError: If *site* is out of range or already occupied.
    """
    self._validate_site(site)
    holder = self._site_holders[site]
    if holder.resource is not None:
      raise ValueError(f"Site {site} is already occupied")
    holder.assign_child_resource(resource)

  def unassign_site(self, site: int) -> None:
    """Remove whatever resource is assigned to a deck site, if any.

    Args:
      site: The deck site to clear, 1 through 9.
    """
    self._validate_site(site)
    holder = self._site_holders[site]
    if holder.resource is not None:
      holder.unassign_child_resource(holder.resource)

  def resource_at_site(self, site: int) -> Optional[Resource]:
    """Return the resource currently assigned to a deck site, if any.

    Args:
      site: The deck site to query, 1 through 9.
    """
    self._validate_site(site)
    return self._site_holders[site].resource

  def site_for_resource(self, resource: Resource) -> Optional[int]:
    """Return the deck site a resource is assigned to, or ``None``.

    Args:
      resource: The resource to look up.
    """
    for site, holder in self._site_holders.items():
      if holder.resource is resource:
        return site
    return None

  def labware_for_site(self, site: int) -> Optional[Labware]:
    """Return the internal labware description for whatever occupies a site.

    Args:
      site: The deck site to describe, 1 through 9.

    Returns:
      The translated :class:`Labware`, or ``None`` if the site is empty.
    """
    resource = self.resource_at_site(site)
    if resource is None:
      return None
    return labware_from_resource(resource)
