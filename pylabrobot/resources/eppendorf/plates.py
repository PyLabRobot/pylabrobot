""" Thermo Fisher & Thermo Fisher Scientific plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.plate import Lid, Plate

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_in_container_2segments_round_vbottom,
  calculate_liquid_volume_container_2segments_round_vbottom
  )


# # # # # # # # # # Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate # # # # # # # # # #

def _compute_volume_from_height_Eppendorf_96_wellplate_250ul_Vb(h: float):
  if h > 20.3:
    raise ValueError(f"Height {h} is too large for" + \
                     "Eppendorf_96_wellplate_250ul_Vb")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=6.74,
    h_cone=8.54,
    h_cylinder=10.96,
    liquid_height=h)


def _compute_height_from_volume_Eppendorf_96_wellplate_250ul_Vb(liquid_volume: float):
  if liquid_volume > 262.5: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for" + \
                     "Eppendorf_96_wellplate_250ul_Vb")
  return round(calculate_liquid_height_in_container_2segments_round_vbottom(
    d=6.74,
    h_cone=8.54,
    h_cylinder=10.96,
    liquid_volume=liquid_volume),3)

# results_testing_optimal_arguments_dict = {
#     "Volume (ul)": [0, 4, 8, 20, 70, 120, 170, 220, 260],
#     "Observed Height (mm)": [0, 0.45, 1.45, 2.55, 6.45, 9.05, 11.6, 13.15, 14.349],
#     "Predicted Height (mm)": [0.0, 2.905012, 3.660086, 4.967501, 7.54213, 9.05511, 10.456574, 11.858037, 12.979208],
#     "Relative Deviation (%)": [None, 545.56, 152.42, 94.80, 16.93, 0.056, 9.86, 9.82, 9.55]
# }


def Eppendorf_96_wellplate_250ul_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=123.0,
  #   size_y=81.0,
  #   size_z=5,
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Eppendorf_96_wellplate_250ul_Vb_Lid",
  # )

def Eppendorf_96_wellplate_250ul_Vb(name: str, with_lid: bool = False) -> Plate:
  """ Eppendorf cat. no.: 0030133374
  - Material: polycarbonate (frame), polypropylene (wells).
  - part of the twin.tec(R) product line.
  - 'Can be divided into 4 segments of 24 wells each to prevent waste and save money'.
  - Colour: clear.
  - Sterilization compatibility & Thermal resistance: read cpnsumables manual in 
    `./engineering_diagrams/`
  - Chemical resistance: ?
  - Cleanliness: ?
  - Automation compatibility: inconsistent information -> says ANSI/SLAS 1-2004 and 4-2004
    compatible, however, engineering_diagram shows footprint is NOT 1-2004 compatible?
  - Plate (apparently) used for NEB's 96-well-format chemically competent cells...
  - total_volume = 250 ul.
  """
  return Plate(
    name=name,
    size_x=123.0,
    size_y=81.0,
    size_z=20.3,
    lid=Eppendorf_96_wellplate_250ul_Vb_Lid(name + "_lid") if with_lid else None,
    model="Eppendorf_96_wellplate_250ul_Vb",
    plate_type="non-skirted",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=6.76,
      dy=8.26,
      dz=0.0, # check that plate is non-skirted
      item_dx=9,
      item_dy=9,
      size_x=5.48,
      size_y=5.48,
      size_z=19.5,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.2, # engineering_diagram says 0.8 but could not replicate
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=(
        _compute_volume_from_height_Eppendorf_96_wellplate_250ul_Vb
      ),
      compute_height_from_volume=(
        _compute_height_from_volume_Eppendorf_96_wellplate_250ul_Vb
      )
    ),
  )

def Eppendorf_96_wellplate_250ul_Vb_L(name: str, with_lid: bool = False) -> Plate:
  return Eppendorf_96_wellplate_250ul_Vb(name=name, with_lid=with_lid)

def Eppendorf_96_wellplate_250ul_Vb_P(name: str, with_lid: bool = False) -> Plate:
  return Eppendorf_96_wellplate_250ul_Vb(name=name, with_lid=with_lid).rotated(90)
