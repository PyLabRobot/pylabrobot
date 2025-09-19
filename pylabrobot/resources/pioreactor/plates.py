"""
Pioreactor 'plate' resource for PyLabRobot.

This models the single-vessel Pioreactor as a *skirted 1×1 Plate* so it can be
assigned to a PlateHolder (e.g. Hamilton_MFX_plateholder_DWP_metal_tapped_10mm_3dprint).

Geometry (mm)
-------------
- External footprint on holder: X=127.74, Y=85.4
- Central vial (A1 well): inner Ø = 23.5, outer Ø = 27.5, depth = 57
- Plate Z (overall height used by PLR for collisions): well depth + plastic
  thickness (default plastic thickness = 1.0 mm)

Usage
-----
>>> from pylabrobot.resources import Coordinate
>>> pr = pioreactor("pr1")
>>> holder.assign_child_resource(pr)          # PlateHolder accepts Plates
>>> await lh.aspirate(pr["A1"], vols=200,
...                   offsets=Coordinate(0, 0, 2.0))  # +2 mm above bottom
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- Imports with version fallbacks -----------------------------------------
try:
  # PLR ≥ 0.10 (preferred)
  from pylabrobot.resources import Plate, Coordinate
  from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
  try:
    # Newer helper
    from pylabrobot.resources.utils import create_ordered_items_2d as _mk_items
  except Exception:
    # Older helper name
    from pylabrobot.resources.utils import create_equally_spaced_2d as _mk_items
except Exception:  # pragma: no cover - older PLR fallback
  from pylabrobot.resources.plate import Plate
  from pylabrobot.resources.coordinate import Coordinate
  from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
  try:
    from pylabrobot.resources.utils import create_ordered_items_2d as _mk_items
  except Exception:
    from pylabrobot.resources.utils import create_equally_spaced_2d as _mk_items


# ---- Physical constants (mm) -------------------------------------------------
PIOREACTOR_SIZE_X = 127.74 # measured
PIOREACTOR_SIZE_Y = 85.40 # measured
# PIOREACTOR_SIZE_Z = 126.0  # measured
PIOREACTOR_SIZE_Z = 132.0  # measured

VIAL_INNER_DIAMETER = 23.5 # spec
VIAL_OUTER_DIAMETER = 27.5 # spec
# VIAL_DEPTH = 57.0 # lower means tip doesn't go as far down
VIAL_DEPTH = 57.0 # spec

# thickness of plastic between holder deck and start of cavity (≈ "dz" in PLR)
DEFAULT_MATERIAL_Z_THICKNESS = 1.0 # measured


@dataclass(frozen=True)
class VialSpec:
  inner_d: float = VIAL_INNER_DIAMETER
  outer_d: float = VIAL_OUTER_DIAMETER
  depth: float = VIAL_DEPTH


class PioreactorPlate(Plate):
  """Pioreactor as a skirted 1×1 Plate with a single circular well (A1)."""

  def __init__(
    self,
    name: str,
    *,
    model: str | None = None,
    vial: VialSpec | None = None,
    material_z_thickness: float = DEFAULT_MATERIAL_Z_THICKNESS,
    plate_type: str = "skirted",   # PlateHolder expects skirted plates
  ):
    vial = vial or VialSpec()

    # Center the single circular well inside the external footprint
    well_d = vial.inner_d
    dx = (PIOREACTOR_SIZE_X - well_d) / 2.0  # left margin to well
    dy = (PIOREACTOR_SIZE_Y - well_d) / 2.0  # front margin to well
    dz = PIOREACTOR_SIZE_Z - vial.depth   # increase means farther up vial; decrease is towards deck

    # Build a 1×1 grid of Wells (A1 only), circular cross-section, flat bottom.
    ordered_items = _mk_items(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=dx,
      dy=dy,
      dz=dz,
      item_dx=0.0,
      item_dy=0.0,
      size_x=well_d,          # diameter in x for CIRCLE
      size_y=well_d,          # diameter in y for CIRCLE
      size_z=vial.depth,      # cavity depth
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      material_z_thickness=material_z_thickness,
    )

    # Plate overall height = cavity depth + bottom plastic thickness
    # plate_size_z = vial.depth + material_z_thickness
    plate_size_z = PIOREACTOR_SIZE_Z

    # Plate ctor signature changed over time ("ordered_items" vs "items").
    try:
      super().__init__(
        name=name,
        size_x=PIOREACTOR_SIZE_X,
        size_y=PIOREACTOR_SIZE_Y,
        size_z=plate_size_z,
        lid=None,
        model=model or self.__class__.__name__,
        ordered_items=ordered_items,
      )
    except TypeError:
      super().__init__(
        name=name,
        size_x=PIOREACTOR_SIZE_X,
        size_y=PIOREACTOR_SIZE_Y,
        size_z=plate_size_z,
        lid=None,
        model=model or self.__class__.__name__,
        items=ordered_items,
      )

    # Some PlateHolders explicitly check this attribute.
    self.plate_type = plate_type

  # ---- convenience -----------------------------------------------------------
  @property
  def A1(self) -> Well:
    """Return the single well (alias)."""
    return self["A1"]

  def recommended_aspirate_offset(self) -> Coordinate:
    """Conservative default: +2 mm above bottom center of the well."""
    return Coordinate(0.0, 0.0, 2.0)


# Factory for backwards compatibility with your earlier code -------------------
def pioreactor(name: str) -> PioreactorPlate:
  """Create a Pioreactor as a 1×1 Plate (skirted)."""
  return PioreactorPlate(name=name)
