""" Porvair plates """

# pylint: disable=invalid-name

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
