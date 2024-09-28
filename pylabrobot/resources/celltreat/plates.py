from typing import Optional
from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_conical_frustum, compute_height_from_volume_cylinder,
  compute_volume_from_height_conical_frustum, compute_volume_from_height_cylinder)

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, Well, WellBottomType


def CellTreat_96_wellplate_350ul_Ub(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  CellTreat cat. no.: 229591
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  # WELL_UBOTTOM_HEIGHT = 2.81 # absolute height of cylindrical segment, measured
  # WELL_DIAMETER = 6.69 # measured

  well_kwargs = {
    "size_x": 6.35,
    "size_y": 6.35,
    "size_z": 10.04,
    "bottom_type": WellBottomType.U,
  }

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.11,
    size_z=14.30,  # without lid
    lid=lid,
    model=CellTreat_96_wellplate_350ul_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.05,  # measured
      dy=7.75,   # measured
      dz=1.92,   # calibrated manually
      item_dx=8.99,
      item_dy=8.99,
      **well_kwargs,
    ),
  )


def CellTreat_96_wellplate_350ul_Ub_Lid(name: str) -> Lid:
  """
  CellTreat cat. no.: 229591
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  return Lid(
    name=name,
    size_x=127.762,
    size_y=85.471,
    size_z=10.71,  # measured
    nesting_z_height=8.30,  # measured as height of plate "plateau"
    model=CellTreat_96_wellplate_350ul_Ub_Lid.__name__,
  )


def CellTreat_6_wellplate_16300ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  CellTreat cat. no.: 229105
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  UPPER_WELL_RADIUS = 17.75 # from plate specs/drawing
  LOWER_WELL_RADIUS = 17.35 # from plate specs/drawing

  well_kwargs = {
    "size_x": 34.7, # from plate specs/drawing
    "size_y": 34.7, # from plate specs/drawing
    "size_z": 17.2, # from plate specs/drawing
    "bottom_type": WellBottomType.FLAT,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, LOWER_WELL_RADIUS, UPPER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, LOWER_WELL_RADIUS, UPPER_WELL_RADIUS
    ),
    "max_volume": 16300, # from spec
  }

  return Plate(
    name=name,
    size_x=127.8, # from plate specs/drawing
    size_y=85.38, # from plate specs/drawing
    size_z=20.2, # from plate specs/drawing
    lid=lid,
    model=CellTreat_6_wellplate_16300ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=6.19, # from plate specs/drawing
      dy=3.65, # from plate specs/drawing
      dz=3, # manually calibrated
      item_dx=39.04, # from plate specs/drawing
      item_dy=39.04, # from plate specs/drawing
      **well_kwargs,
    ),
  )


def CellTreat_6_wellplate_16300ul_Fb_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.0, # from plate specs/drawing
    size_y=84.8, # from plate specs/drawing
    size_z=10.20, # measured
    nesting_z_height=9.0, # measured as difference between 2-stack and single
    model=CellTreat_6_wellplate_16300ul_Fb_Lid.__name__,
  )


def CellTreat_96_wellplate_U(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  CellTreat cat. no.: 229590
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  WELL_RADIUS = 3.175

  well_kwargs = {
    "size_x": 6.35,
    "size_y": 6.35,
    "size_z": 10.04,
    "bottom_type": WellBottomType.U,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_cylinder(
      liquid_height, WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_cylinder(
      liquid_volume, WELL_RADIUS
    ),
    "material_z_thickness": 1.55,
    "cross_section_type": CrossSectionType.CIRCLE,
    "max_volume": 300,
  }

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.11,
    size_z=14.30,  # without lid
    lid=lid,
    model=CellTreat_96_wellplate_U.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.7,  # measured
      dy=8.75,  # measured
      dz=2.6,  # calibrated manually
      item_dx=9,
      item_dy=9,
      **well_kwargs,
    ),
  )
