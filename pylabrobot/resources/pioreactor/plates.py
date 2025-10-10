from typing import Optional

from pylabrobot.resources import Plate, Lid
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType

def pioreactor(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Pioreactor 20mL Vessel: https://pioreactor.com/products/pioreactor-20ml
  Modeled as a 1x1 skirted plate

  Geometry (mm):
  - Outer footprint (on holder): 127.74 x 85.40
  - Total height (plate Z): 126.5
  - Central vial (A1): inner Ø 23.5, depth 57.0
  """

  # --- Outer dimensions (measured) ---
  OUTER_X = 127.74
  OUTER_Y = 85.40
  OUTER_Z = 126.5  # overall height used for collision checks

  # --- Well (vial) geometry (spec/measured) ---
  WELL_DIAMETER = 23.5
  WELL_DEPTH = 57.0
  MATERIAL_Z_THICKNESS = 1.0  # plastic between top surface and cavity start

  # Center the single circular well
  dx = (OUTER_X - WELL_DIAMETER) / 2.0
  dy = (OUTER_Y - WELL_DIAMETER) / 2.0

  # Distance from plate top to top of cavity
  # dz = OUTER_Z - WELL_DEPTH - MATERIAL_Z_THICKNESS
  # dz = 126.5-57 -1 =68.5 # tip crashes into bottom
  dz = 76 # measured

  # Cylinder area for volume/height conversions
  cross_section_area = 3.14 * (WELL_DIAMETER / 2.0) ** 2

  well_kwargs = {
    "size_x": WELL_DIAMETER,               # for CIRCLE, size_x == size_y == diameter
    "size_y": WELL_DIAMETER,
    "size_z": WELL_DEPTH,
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_height_from_volume": lambda v: v / cross_section_area,
    "compute_volume_from_height": lambda h: h * cross_section_area,
    "material_z_thickness": MATERIAL_Z_THICKNESS,
  }

  return Plate(
    name=name,
    size_x=OUTER_X,
    size_y=OUTER_Y,
    size_z=OUTER_Z,
    lid=lid,
    model="PioreactorPlate",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=dx,
      dy=dy,
      dz=dz,
      item_dx=WELL_DIAMETER,
      item_dy=WELL_DIAMETER,
      **well_kwargs,
    ),
  )
