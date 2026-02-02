from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # Eppendorf_96_wellplate_250ul_Vb # # # # # # # # # #


def _compute_volume_from_height_Eppendorf_96_wellplate_250ul_Vb(
  h: float,
):
  if h > 20.3:
    raise ValueError(f"Height {h} is too large for" + "Eppendorf_96_wellplate_250ul_Vb")
  return max(
    0.89486648 + 2.92455131 * h + 2.03472797 * h**2 + -0.16509371 * h**3 + 0.00675759 * h**4,
    0,
  )


def _compute_height_from_volume_Eppendorf_96_wellplate_250ul_Vb(
  liquid_volume: float,
):
  if liquid_volume > 262.5:  # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for" + "Eppendorf_96_wellplate_250ul_Vb")
  return max(
    0.118078503
    + 0.133333914 * liquid_volume
    + -0.000802726227 * liquid_volume**2
    + 3.29761957e-06 * liquid_volume**3
    + -5.29119614e-09 * liquid_volume**4,
    0,
  )


# results_measurement_fitting_dict = {
#     "Volume (ul)": [0, 4, 8, 20, 70, 120, 170, 220, 260],
#     "Observed Height (mm)": [0, 0.45, 1.45, 2.55, 6.45, 9.05, 11.6, 13.15, 14.349],
#     "Predicted Height (mm)": [0.118, 0.637, 1.139, 2.49, 6.516, 9.177, 11.359, 13.31, 14.291],
#     "Relative Deviation (%)": [0, 41.525, -21.793, -2.365, 1.024, 1.399, -2.078, 1.216, -0.408]
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
  """Eppendorf cat. no.: 0030133374
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
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=6.76,
      dy=8.26,
      dz=0.0,  # check that plate is non-skirted
      item_dx=9,
      item_dy=9,
      size_x=5.48,
      size_y=5.48,
      size_z=19.5,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.2,  # engineering_diagram says 0.8 but could not replicate
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=(_compute_volume_from_height_Eppendorf_96_wellplate_250ul_Vb),
      compute_height_from_volume=(_compute_height_from_volume_Eppendorf_96_wellplate_250ul_Vb),
    ),
  )


def eppendorf_96_wellplate_1000ul_Vb(name: str) -> Plate:
  """Eppendorf Deepwell Plate 96/1000uL, cat. no.: 951032921"""

  material_z_thickness = 1.05  # measured with ztouch_probe_z_height_using_channel
  well_diameter_top = 7.0  # from spec

  return Plate(
    name=name,
    size_x=127.8,  # w
    size_y=85.5,  # l
    size_z=44.1,  # h
    lid=None,
    model="eppendorf_96_wellplate_1000ul_Vb",
    plate_type="skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - well_diameter_top / 2,  # P1 - well width / 2
      dy=11.2 - well_diameter_top / 2,  # P3 - well width / 2
      dz=3.2 - material_z_thickness,  # b - material_z_thickness
      item_dx=9.0,  # P2: well spacing in x
      item_dy=9.0,  # P4: well spacing in y
      size_x=well_diameter_top,  # well top diameter
      size_y=well_diameter_top,
      size_z=38.7 + 2.4,  # well depth: h1 + h2
      bottom_type=WellBottomType.V,
      material_z_thickness=material_z_thickness,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=None,
      compute_height_from_volume=None,
    ),
  )
