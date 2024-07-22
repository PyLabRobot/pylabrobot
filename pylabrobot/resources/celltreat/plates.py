from typing import Optional
from pylabrobot.resources.height_volume_functions import (compute_height_from_volume_cylinder,
                                                          compute_volume_from_height_cylinder)

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_equally_spaced_2d
from pylabrobot.resources.well import Well, WellBottomType


def CellTreat_96_WP_U(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  CellTreat cat. no.: 229590
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  WELL_UBOTTOM_HEIGHT = 2.81 # absolute height of cylindrical segment, measured
  WELL_DIAMETER = 6.69 # measured

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.11,
    size_z=14.30,  # without lid
    lid=lid,
    model=CellTreat_96_WP_U.__name__,
    items=create_equally_spaced_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.05,  # measured
      dy=7.75,  # measured
      dz=1.92,  # calibrated manually
      item_dx=8.99,
      item_dy=8.99,
      size_x=6.35,
      size_y=6.35,
      size_z=10.04,
      bottom_type=WellBottomType.U,
    ),
  )


def CellTreat_96_WP_U_Lid(name: str) -> Lid:
  """
  CellTreat cat. no.: 229590
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  return Lid(
    name=name,
    size_x=127.762,
    size_y=85.471,
    size_z=10.71,  # measured
    nesting_z_height=8.30,  # measured as height of plate "plateau"
    model=CellTreat_96_WP_U_Lid.__name__,
  )


def CellTreat_6_WP_Flat(name: str, lid: Optional[Lid] = None) -> Plate:
  WELL_RADIUS = 17.5

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=20.5,
    lid=lid,
    model=CellTreat_6_WP_Flat.__name__,
    items=create_equally_spaced_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=6,
      dy=5,
      dz=3,
      item_dx=38.5,
      item_dy=38.5,
      size_x=38.5,
      size_y=38.5,
      size_z=18.5,
      bottom_type=WellBottomType.FLAT,
      compute_volume_from_height=lambda liquid_height: compute_volume_from_height_cylinder(
        liquid_height, WELL_RADIUS
      ),
      compute_height_from_volume=lambda liquid_volume: compute_height_from_volume_cylinder(
        liquid_volume, WELL_RADIUS
      ),
    ),
  )


def CellTreat_6_WP_Flat_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=9,
    nesting_z_height=7.9,
    model="CellTreat_6_WP_Rd_Lid",
  )
