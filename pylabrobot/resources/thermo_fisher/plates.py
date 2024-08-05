""" Thermo Fisher & Thermo Fisher Scientific plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.plate import Lid, Plate

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_in_container_2segments_square_ubottom,
  calculate_liquid_volume_container_2segments_square_ubottom,
  calculate_liquid_height_in_container_2segments_round_vbottom,
  calculate_liquid_volume_container_2segments_round_vbottom
  )


# # # # # # # # # # ThermoScientific_96_wellplate_1200ul_Rd # # # # # # # # # #

def _compute_volume_from_height_ThermoScientific_96_wellplate_1200ul_Rd(h: float):
  if h > 20.5:
    raise ValueError(f"Height {h} is too large for" + \
                     "ThermoScientific_96_wellplate_1200ul_Rd")
  return calculate_liquid_volume_container_2segments_square_ubottom(
    x=8.15,
    h_cuboid=16.45,
    liquid_height=h)


def _compute_height_from_volume_ThermoScientific_96_wellplate_1200ul_Rd(liquid_volume: float):
  if liquid_volume > 1260: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for" + \
                     "ThermoScientific_96_wellplate_1200ul_Rd")
  return round(calculate_liquid_height_in_container_2segments_square_ubottom(
    x=8.15,
    h_cuboid=16.45,
    liquid_volume=liquid_volume),3)


def ThermoScientific_96_wellplate_1200ul_Rd_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="ThermoScientific_96_DWP_1200ul_Rd_Lid",
  # )

def ThermoScientific_96_DWP_1200ul_Rd(name: str, with_lid: bool = False) -> Plate:
  raise NotImplementedError("This function is deprecated and will be removed in a future version."
          " Use 'ThermoScientific_96_wellplate_1200ul_Rd' instead.")


def ThermoScientific_96_wellplate_1200ul_Rd(name: str, with_lid: bool = False) -> Plate:
  """ Fisher Scientific/Thermo Fisher cat. no.: 10243223/AB1127.
  - Material: Polypropylene (AB-1068, polystyrene)
  - Sterilization compatibility: Autoclaving (15 minutes at 121°C) or
    Gamma Irradiation
  - Chemical resistance: to DMSO (100%); Ethanol (100%); Isopropanol (100%)
  - Round well shape designed for optimal sample recovery or square shape to
    maximize sample volume within ANSI footprint design
  - Each well has an independent sealing rim to prevent cross-contamination
  - U-bottomed wells ideally suited for sample resuspension
  - Sealing options: Adhesive Seals, Heat Seals, Storage Plate Caps and Cap
    Strips, and Storage Plate Sealing Mats
  - Cleanliness: 10243223/AB1127: Cleanroom manufacture
  - ANSI/SLAS-format for compatibility with automated systems
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=24.0,
    lid=ThermoScientific_96_wellplate_1200ul_Rd_Lid(name + "_lid") if with_lid else None,
    model="ThermoScientific_96_wellplate_1200ul_Rd",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=7.3,
      dz=1.0, # 2.5. https://github.com/PyLabRobot/pylabrobot/pull/183
      item_dx=9,
      item_dy=9,
      size_x=8.3,
      size_y=8.3,
      size_z=20.5,
      bottom_type=WellBottomType.U,
      material_z_thickness=1.0,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=(
        _compute_volume_from_height_ThermoScientific_96_wellplate_1200ul_Rd
      ),
      compute_height_from_volume=(
        _compute_height_from_volume_ThermoScientific_96_wellplate_1200ul_Rd
      )
    ),
  )

def ThermoScientific_96_wellplate_1200ul_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_wellplate_1200ul_Rd(name=name, with_lid=with_lid)

def ThermoScientific_96_wellplate_1200ul_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_wellplate_1200ul_Rd(name=name, with_lid=with_lid).rotated(90)



# # # # # # # # # # Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate # # # # # # # # # #

def _compute_volume_from_height_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(h: float):
  if h > 21.1:
    raise ValueError(f"Height {h} is too large for" + \
                     "ThermoScientific_96_wellplate_1200ul_Rd")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=5.5,
    h_cone=13.6,
    h_cylinder=7.5,
    liquid_height=h)


def _compute_height_from_volume_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(liquid_volume: float):
  if liquid_volume > 315: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for" + \
                     "ThermoScientific_96_wellplate_1200ul_Rd")
  return round(calculate_liquid_height_in_container_2segments_round_vbottom(
    d=5.5,
    h_cone=13.6,
    h_cylinder=7.5,
    liquid_volume=liquid_volume),3)


def Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_Lid",
  # )

def Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(name: str, with_lid: bool = False) -> Plate:
  """ Thermo Fisher Scientific/Fisher Scientific cat. no.: 4483354/15273005 (= with barcode)
  - Part no.: 16698853 (FS) (= **without** barcode).
  - Material: Polycarbonate, Polypropylene.
  - Sterilization compatibility: ?
  - Chemical resistance: ?
  - Thermal resistance: ?
  - Cleanliness: 'Certified DNA-, RNAse-, and PCR inhibitor-free with in-process sampling tests'.
  - ANSI/SLAS-format for compatibility with automated systems.
  - total_volume = 300 ul.
  - working_volume = 200 ul (recommended by manufacturer).
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=20.1+1.0 - 0.5,
    lid=Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_Lid(name + "_lid") if with_lid else None,
    model="Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate",
    plate_type="semi-skirted",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.63,
      dy=9.95,
      dz=0.0, # check that plate is semi-skirted
      item_dx=9,
      item_dy=9,
      size_x=5.49,
      size_y=5.49,
      size_z=20.5,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.0,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=(
        _compute_volume_from_height_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate
      ),
      compute_height_from_volume=(
        _compute_height_from_volume_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate
      )
    ),
  )

def Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_L(name: str, with_lid: bool = False) -> Plate:
  return Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(name=name, with_lid=with_lid)

def Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_P(name: str, with_lid: bool = False) -> Plate:
  return Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(name=name, with_lid=with_lid).rotated(90)
