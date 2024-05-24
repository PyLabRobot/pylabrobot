""" Thermo Fisher & Thermo Fisher Scientific plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.plate import Plate

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_square_ubottom
from pylabrobot.resources.height_functions import calculate_liquid_height_in_container_2segments_square_ubottom


# # # # # # # # # # ThermoScientific_96_1200ul_Rd # # # # # # # # # #

def _compute_volume_from_height_ThermoScientific_96_1200ul_Rd(h: float):
  if h > 20.5:
    raise ValueError(f"Height {h} is too large for ThermoScientific_96_1200ul_Rd")
  return calculate_liquid_volume_container_2segments_square_ubottom(
    x=8.15,
    h_cuboid=16.45,
    liquid_height=h)

def _compute_height_from_volume_ThermoScientific_96_1200ul_Rd(liquid_volume: float):
  if liquid_volume > 1260:
    raise ValueError(f"Volume {liquid_volume} is too large for ThermoScientific_96_1200ul_Rd")
  return round(calculate_liquid_height_in_container_2segments_square_ubottom(
    x=8.15,
    h_cuboid=16.45,
    liquid_volume=liquid_volume),3)

def ThermoScientific_96_1200ul_Rd(name: str, with_lid: bool = False) -> Plate:
  """ Fisher Scientific/Thermo Fisher cat. no.: 10243223/AB1127.
  - Material: Polypropylene (AB-1068, polystyrene)
  - Suitable for Autoclaving (15 minutes at 121°C) or Gamma Irradiation
  - Resistant to DMSO (100%); Ethanol (100%); Isopropanol (100%)
  - Round well shape designed for optimal sample recovery or square shape to
    maximize sample volume within ANSI footprint design
  - Each well has an independent sealing rim to prevent cross-contamination
  - U-bottomed wells ideally suited for sample resuspension
  - Sealing options: Adhesive Seals, Heat Seals, Storage Plate Caps and Cap
    Strips, and Storage Plate Sealing Mats
  - Cleanroom manufactured
  - ANSI-format for compatibility with automated systems
  """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=24.0,
    with_lid=with_lid,
    model="ThermoScientific_96_1200ul_Rd",
    lid_height=5,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.6,
      dy=7.3,
      dz=0.2,
      item_dx=9,
      item_dy=9,
      size_x=8.3,
      size_y=8.3,
      size_z=20.5,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_ThermoScientific_96_1200ul_Rd,
      compute_height_from_volume=_compute_height_from_volume_ThermoScientific_96_1200ul_Rd
    ),
  )

#: ThermoScientific_96_1200ul_Rd_L
def ThermoScientific_96_1200ul_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_1200ul_Rd(name=name, with_lid=with_lid)

#: ThermoScientific_96_1200ul_Rd_P
def ThermoScientific_96_1200ul_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_1200ul_Rd(name=name, with_lid=with_lid).rotated(90)
