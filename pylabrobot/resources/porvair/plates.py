""" Porvair plates """

# pylint: disable=invalid-name

from typing import Optional
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_in_container_2segments_square_vbottom,
  calculate_liquid_volume_container_2segments_square_vbottom)

# # # # # # # # # # Porvair_6_reservoir_47ml_Vb # # # # # # # # # #

def _compute_volume_from_height_Porvair_6_reservoir_47ml_Vb(h: float):
  if h > 42.5:
    raise ValueError(f"Height {h} is too large for Porvair_6_reservoir_47ml_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=17,
    y=70.8,
    h_pyramid=5,
    h_cube=37.5,
    liquid_height=h)


def _compute_height_from_volume_Porvair_6_reservoir_47ml_Vb(liquid_volume: float):
  if liquid_volume > 49_350.0: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for Porvair_6_reservoir_47ml_Vb")
  return round(calculate_liquid_height_in_container_2segments_square_vbottom(
    x=17,
    y=70.8,
    h_pyramid=5,
    h_cube=37.5,
    liquid_volume=liquid_volume),3)


def Porvair_6_reservoir_47ml_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Porvair_6_reservoir_47ml_Vb_Lid",
  # )


#: Porvair_6_reservoir_47ml_Vb
def Porvair_6_reservoir_47ml_Vb(name: str, with_lid: bool = False) -> Plate:
  """ Porvair cat. no.: 390015.
  - Material: Polypropylene
  - Sterilization compatibility: Autoclaving (15 minutes at 121°C) or Gamma Irradiation
  - Chemical resistance: "High chemical resistance"
  - Temperature resistance: high: -196°C to + 121°C
  - Cleanliness: 390015: Free of detectable DNase, RNase
  - ANSI/SLAS-format for compatibility with automated systems
  - Tolerances: "Uniform external dimensions and tolerances"
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=44,
    lid=Porvair_6_reservoir_47ml_Vb_Lid(name + "_lid") if with_lid else None,
    model="Porvair_6_reservoir_47ml_Vb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=6,
      num_items_y=1,
      dx=9.3,
      dy=5.7,
      dz=2.24,
      item_dx= 18.5,
      item_dy=6.9,
      size_x=16.8,
      size_y=70.8,
      size_z=42.5,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Porvair_6_reservoir_47ml_Vb,
      compute_height_from_volume=_compute_height_from_volume_Porvair_6_reservoir_47ml_Vb
    ),
  )


#: Porvair_6_reservoir_47ml_Vb_L
def Porvair_6_reservoir_47ml_Vb_L(name: str, with_lid: bool = False) -> Plate:
  return Porvair_6_reservoir_47ml_Vb(name=name, with_lid=with_lid)


#: Porvair_6_reservoir_47ml_Vb_P
def Porvair_6_reservoir_47ml_Vb_P(name: str, with_lid: bool = False) -> Plate:
  return Porvair_6_reservoir_47ml_Vb(name=name, with_lid=with_lid).rotated(z=90)


def Porvair_24_wellplate_Vb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Porvair cat. no.: 390108
  - Material: Polypropylene
  - Tissue culture treated: No
  """
  WELL_SIZE_X = 8.0
  WELL_SIZE_Y = 35.0
  WELL_HEIGHT_OF_PYRAMID = 11.65
  WELL_HEIGHT_OF_CUBE = 1.65

  well_kwargs = {
    "size_x": 8.0,
    "size_y": 35.0,
    "size_z": 13.30,
    # reality: multifaceted pyramid, v bottom pyramid is a good approximation
    "bottom_type": WellBottomType.V,
    "compute_volume_from_height":
      lambda liquid_height: calculate_liquid_volume_container_2segments_square_vbottom(
      WELL_SIZE_X, WELL_SIZE_Y, WELL_HEIGHT_OF_PYRAMID, WELL_HEIGHT_OF_CUBE, liquid_height
    ),
    "compute_height_from_volume": lambda liquid_volume: (
      calculate_liquid_height_in_container_2segments_square_vbottom(
        WELL_SIZE_X, WELL_SIZE_Y, WELL_HEIGHT_OF_PYRAMID, WELL_HEIGHT_OF_CUBE, liquid_volume
      )
    ),
    "material_z_thickness": 1.0,  # measured
    "cross_section_type": CrossSectionType.RECTANGLE,
    "max_volume": 3500,  # according to porvair spec
  }

  return Plate(
    name=name,
    size_x=127.5,  # measured
    size_y=85.3,  # measured
    size_z=19.2,  # measured
    lid=lid,
    model=Porvair_24_wellplate_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=2,
      dx=9.90,  # measured
      dy=6.85,  # measured
      dz=5.8,  # measured and calibrated manually
      item_dx=9.05,
      item_dy=36,
      **well_kwargs,
    ),
  )
