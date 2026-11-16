import math

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # Eppendorf_96_wellplate_250ul_Vb # # # # # # # # # #


_Eppendorf_96_wellplate_250ul_Vb_well_depth = 19.5
_Eppendorf_96_wellplate_250ul_Vb_well_diameter_top = 5.48

# Measured liquid heights (mm) for dispensed volumes (uL) up to 14.349 mm. Between the last
# measurement and the well rim the well is a cylinder of the rim's inner diameter: a tapered well is
# widest at its rim, so this is the largest cross-section the drawing allows.
_Eppendorf_96_wellplate_250ul_Vb_height_volume_data = {
  0.0: 0.0,
  0.45: 4.0,
  1.45: 8.0,
  2.55: 20.0,
  6.45: 70.0,
  9.05: 120.0,
  11.6: 170.0,
  13.15: 220.0,
  14.349: 260.0,
  _Eppendorf_96_wellplate_250ul_Vb_well_depth: 260.0
  + math.pi
  * (_Eppendorf_96_wellplate_250ul_Vb_well_diameter_top / 2) ** 2
  * (_Eppendorf_96_wellplate_250ul_Vb_well_depth - 14.349),
}


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
      size_x=_Eppendorf_96_wellplate_250ul_Vb_well_diameter_top,
      size_y=_Eppendorf_96_wellplate_250ul_Vb_well_diameter_top,
      size_z=_Eppendorf_96_wellplate_250ul_Vb_well_depth,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.2,  # engineering_diagram says 0.8 but could not replicate
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=_Eppendorf_96_wellplate_250ul_Vb_height_volume_data[
        _Eppendorf_96_wellplate_250ul_Vb_well_depth
      ],
      height_volume_data=_Eppendorf_96_wellplate_250ul_Vb_height_volume_data,
      name_prefix=name,
    ),
  )


def eppendorf_96_wellplate_500ul_Vb(name: str) -> Plate:
  """Eppendorf Deepwell Plate 96/500uL, cat. no.: 951032107

  https://archive.vn/WCuMF
  """

  material_z_thickness = 1.05  # measured with ztouch_probe_z_height_using_channel
  well_diameter_top = 7.0  # from spec

  return Plate(
    name=name,
    size_x=127.8,  # w
    size_y=85.5,  # l
    size_z=27.1,  # h
    lid=None,
    model=eppendorf_96_wellplate_500ul_Vb.__name__,
    plate_type="skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - well_diameter_top / 2,  # P1 - well width / 2
      dy=11.2 - well_diameter_top / 2,  # P3 - well width / 2
      dz=2.1 - material_z_thickness,  # b - material_z_thickness
      item_dx=9.0,
      item_dy=9.0,
      size_x=well_diameter_top,
      size_y=well_diameter_top,
      size_z=21.4 + 2.6,  # well depth: h1 + h2
      bottom_type=WellBottomType.V,
      material_z_thickness=material_z_thickness,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=None,
      compute_height_from_volume=None,
      name_prefix=name,
    ),
  )


def eppendorf_96_wellplate_1000ul_Vb(name: str) -> Plate:
  """Eppendorf Deepwell Plate 96/1000uL, cat. no.: 951032921

  https://archive.vn/uMRwv
  """

  material_z_thickness = 1.05  # measured with ztouch_probe_z_height_using_channel
  well_diameter_top = 7.0  # from spec

  return Plate(
    name=name,
    size_x=127.8,  # w
    size_y=85.5,  # l
    size_z=44.1,  # h
    lid=None,
    model=eppendorf_96_wellplate_1000ul_Vb.__name__,
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
      name_prefix=name,
    ),
  )


def eppendorf_96_wellplate_2000ul_vb(name: str) -> Plate:
  """Eppendorf Deepwell Plate 96/2000 µL

  https://archive.vn/wSbxT
  """

  material_z_thickness = 1.3  # measured with ztouch_probe_z_height_using_channel
  well_diameter_top = 8.2  # from spec (d)

  return Plate(
    name=name,
    size_x=127.8,  # l
    size_y=85.5,  # w
    size_z=44.1,  # h
    lid=None,
    model=eppendorf_96_wellplate_2000ul_vb.__name__,
    plate_type="skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - well_diameter_top / 2,  # P1 - well width / 2
      dy=11.2 - well_diameter_top / 2,  # P3 - well width / 2
      dz=1.5 - material_z_thickness,  # b - material_z_thickness (2000 µL spec)
      item_dx=9.0,  # P2
      item_dy=9.0,  # P4
      size_x=well_diameter_top,
      size_y=well_diameter_top,
      size_z=38.1 + 3.1,  # h1 + h2 (2000 µL spec)
      bottom_type=WellBottomType.V,
      material_z_thickness=material_z_thickness,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=None,
      compute_height_from_volume=None,
      name_prefix=name,
    ),
  )
