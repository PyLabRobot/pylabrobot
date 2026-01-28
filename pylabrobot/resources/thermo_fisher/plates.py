"""Thermo Fisher Scientific  Inc. (and all its brand) plates"""

import math

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_in_container_2segments_square_ubottom,
  calculate_liquid_volume_container_2segments_square_ubottom,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# Please conform with the 'manufacturer-first, then brands' naming principle:

# Thermo Fisher Scientific Inc. (TFS, aka "Thermo")
# ├── Applied Biosystems (AB; brand)
# │   └── MicroAmp
# │      └── EnduraPlate
# ├── Fisher Scientific (FS; brand)
# ├── Invitrogen (INV; brand)
# ├── Ion Torrent (IT; brand)
# ├── Gibco (GIB; brand)
# ├── Thermo Scientific (TS; brand)
# │   ├── Nalgene
# │   ├── Nunc
# │   └── Pierce
# ├── Unity Lab Services (brand, services)
# ├── Patheon (brand, services)
# └── PPD (brand, services)


# # # # # # # # # # Thermo_TS_96_wellplate_1200ul_Rb # # # # # # # # # #


def _compute_volume_from_height_Thermo_TS_96_wellplate_1200ul_Rb(
  h: float,
):
  if h > 20.5:
    raise ValueError(f"Height {h} is too large for" + "Thermo_TS_96_wellplate_1200ul_Rb")
  return calculate_liquid_volume_container_2segments_square_ubottom(
    x=8.15, h_cuboid=16.45, liquid_height=h
  )


def _compute_height_from_volume_Thermo_TS_96_wellplate_1200ul_Rb(
  liquid_volume: float,
):
  if liquid_volume > 1260:  # 5% tolerance
    raise ValueError(
      f"Volume {liquid_volume} is too large for" + "Thermo_TS_96_wellplate_1200ul_Rb"
    )
  return round(
    calculate_liquid_height_in_container_2segments_square_ubottom(
      x=8.15, h_cuboid=16.45, liquid_volume=liquid_volume
    ),
    3,
  )


def Thermo_TS_96_wellplate_1200ul_Rb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Thermo_TS_96_wellplate_1200ul_Rb_Lid",
  # )


def Thermo_TS_96_wellplate_1200ul_Rb(name: str, with_lid: bool = False) -> Plate:
  """Thermo Fisher Scientific/Fisher Scientific cat. no.: AB1127/10243223.
  - Material: Polypropylene (AB-1068, polystyrene).
  - Brand: Thermo Scientific.
  - Sterilization compatibility: Autoclaving (15 minutes at 121°C) or
    Gamma Irradiation.
  - Chemical resistance: to DMSO (100%); Ethanol (100%); Isopropanol (100%).
  - Round well shape designed for optimal sample recovery or square shape to
    maximize sample volume within ANSI footprint design.
  - Each well has an independent sealing rim to prevent cross-contamination
  - U-bottomed wells ideally suited for sample resuspension.
  - Sealing options: Adhesive Seals, Heat Seals, Storage Plate Caps and Cap
    Strips, and Storage Plate Sealing Mats.
  - Cleanliness: 10243223/AB1127: Cleanroom manufacture.
  - ANSI/SLAS-format for compatibility with automated systems.
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=24.0,
    lid=Thermo_TS_96_wellplate_1200ul_Rb_Lid(name + "_lid") if with_lid else None,
    model="Thermo_TS_96_wellplate_1200ul_Rb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=7.3,
      dz=2.5,  # 2.5. https://github.com/PyLabRobot/pylabrobot/pull/183
      item_dx=9,
      item_dy=9,
      size_x=8.3,
      size_y=8.3,
      size_z=20.5,
      bottom_type=WellBottomType.U,
      material_z_thickness=1.15,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=(_compute_volume_from_height_Thermo_TS_96_wellplate_1200ul_Rb),
      compute_height_from_volume=(_compute_height_from_volume_Thermo_TS_96_wellplate_1200ul_Rb),
    ),
  )


# # # # # # # # # # Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate # # # # # # # # # #


def _compute_volume_from_height_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(
  h: float,
):
  if h > 21.1:
    raise ValueError(f"Height {h} is too large for" + "ThermoScientific_96_wellplate_1200ul_Rd")
  return max(
    0.9617 + 10.2590 * h - 1.3069 * h**2 + 0.26799 * h**3 - 0.01003 * h**4,
    0,
  )


def _compute_height_from_volume_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate(
  liquid_volume: float,
):
  if liquid_volume > 315:  # 5% tolerance
    raise ValueError(
      f"Volume {liquid_volume} is too large for" + "ThermoScientific_96_wellplate_1200ul_Rd"
    )
  return max(
    -0.1823
    + 0.1327 * liquid_volume
    - 0.000637 * liquid_volume**2
    + 1.6577e-6 * liquid_volume**3
    - 1.1487e-9 * liquid_volume**4,
    0,
  )


# results_measurement_fitting_dict = {
#     "Volume (ul)": [0, 4, 8, 20, 70, 120, 170, 220, 260],
#     "Observed Height (mm)": [0, 0.17, 0.77, 2.27, 6.57, 9.17, 11.17, 13.17, 15.17],
#     "Predicted Height (mm)": [0, 0.338, 0.839, 2.230, 6.526, 9.195, 11.152, 13.141, 15.145],
#     "Relative Deviation (%)": [0, 99.07, 9.01, -1.76, -0.66, 0.27, -0.16, -0.22, -0.17]
# }


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
  """Thermo Fisher Scientific/Fisher Scientific cat. no.: 4483354/15273005 (= with barcode)
  - alternative cat. no.: 16698853 (FS) (= **without** barcode).
  - See `./engineering_diagrams/` directory for more part numbers (different colours).
  - Material: Polycarbonate, Polypropylene
  - Sterilization compatibility: ?
  - Chemical resistance: ?
  - Thermal resistance: ?
  - Cleanliness: 'Certified DNA-, RNAse-, and PCR inhibitor-free with in-process sampling tests'.
  - ANSI/SLAS-format for compatibility with automated systems.
  - optimal pickup_distance_from_top=4 mm.
  - total_volume = 300 ul.
  - working_volume = 200 ul (recommended by manufacturer).
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=20.1 + 1.6 - 0.5,  # cavity_depth + material_z_thickness - well_extruding_over_plate
    lid=Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate_Lid(name + "_lid") if with_lid else None,
    model="Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate",
    plate_type="semi-skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.63,
      dy=9.95,
      dz=0.0,  # check that plate is semi-skirted
      item_dx=9,
      item_dy=9,
      size_x=5.49,
      size_y=5.49,
      size_z=20.1,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.6,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=(
        _compute_volume_from_height_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate
      ),
      compute_height_from_volume=(
        _compute_height_from_volume_Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate
      ),
    ),
  )


# # # # # # # # # # Thermo_Nunc_96_well_plate_1300uL_Rb # # # # # # # # # #


def Thermo_Nunc_96_well_plate_1300uL_Rb(name: str) -> Plate:
  """
  - Part no.: 260252
  - Diagram: https://assets.thermofisher.com/TFS-Assets/LSG/manuals/D03011.pdf
  """

  well_diameter = 8.00  # measured
  return Plate(
    name=name,
    size_x=127.76,  # from definition, A
    size_y=85.47,  # from definition, B
    size_z=31.6,  # from definition, F
    lid=None,
    model=Thermo_Nunc_96_well_plate_1300uL_Rb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - well_diameter / 2,  # from definition, H - well_diameter/2
      dy=11.2 - well_diameter / 2,  # from definition, J - well_diameter/2
      dz=1.4,  # from definition, N
      item_dx=9,
      item_dy=9,
      size_x=well_diameter,
      size_y=well_diameter,
      size_z=31.6 - 1.4,  # from definition, F - N
      bottom_type=WellBottomType.U,
      material_z_thickness=31.6 - 29.1 - 1.4,  # from definition, F - L - N
      cross_section_type=CrossSectionType.CIRCLE,
      compute_height_from_volume=lambda liquid_volume: liquid_volume
      / (math.pi * ((well_diameter / 2) ** 2)),
    ),
  )


# # # # # # # # # # thermo_AB_96_wellplate_300ul_Vb_MicroAmp # # # # # # # # # #


def _compute_volume_from_height_thermo_AB_96_wellplate_300ul_Vb_MicroAmp(height_mm: float) -> float:
  if height_mm > (23.24 - 0.74) * 1.05:
    raise ValueError(
      f"Height {height_mm} is too large for " "thermo_AB_96_wellplate_300ul_Vb_MicroAmp"
    )
  # Reverse fit: height → volume, 5th-degree polynomial via numeric inversion
  return max(
    -6.7862
    + 2.7847 * height_mm
    - 0.17352 * height_mm**2
    + 0.006029 * height_mm**3
    - 9.971e-5 * height_mm**4
    + 6.451e-7 * height_mm**5,
    0,
  )


def _compute_height_from_volume_thermo_AB_96_wellplate_300ul_Vb_MicroAmp(volume_ul: float) -> float:
  if volume_ul > 305:  # 5% tolerance above 290 µL
    raise ValueError(
      f"Volume {volume_ul} is too large for " "thermo_AB_96_wellplate_300ul_Vb_MicroAmp"
    )
  # Polynomial coefficients: degree 5 fit from volume → height
  return max(
    1.0796
    + 0.1570 * volume_ul
    - 0.00099828 * volume_ul**2
    + 3.4541e-6 * volume_ul**3
    - 3.5805e-9 * volume_ul**4
    - 1.8018e-12 * volume_ul**5,
    0,
  )


# results_measurement_fitting_dict = {
#  'Volume (ul)': [4, 8, 20, 40, 70, 120, 170, 220, 260, 290],
#  'Observed Height (mm)': [1.69, 2.29, 3.89, 5.79, 8.49, 10.59, 12.69, 14.79, 16.59, 17.89]
# }


def thermo_AB_96_wellplate_300ul_Vb_MicroAmp_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")


def thermo_AB_96_wellplate_300ul_Vb_MicroAmp(name: str, with_lid: bool = False) -> Plate:
  """Thermo Fisher Scientific cat. no.: N8010560/4316813 (w/o barcode)
  - alternative cat. no.: 4306737/4326659 (with barcode).
  - See `./engineering_diagrams/` directory for more part numbers.
  - Material: Polypropylene.
  - Sterilization compatibility: ?
  - Chemical resistance: ?
  - Thermal resistance: ?
  - Cleanliness: 'Certified DNA/RNase Free'.
  - Warning: NOT ANSI/SLAS-format!
  - optimal pickup_distance_from_top = 6 mm.
  - total_volume = 300 ul.
  - working_volume = 200 ul (recommended by manufacturer).

  https://documents.thermofisher.com/TFS-Assets/LSG/manuals/cms_042421.pdf
  """
  return Plate(
    name=name,
    size_x=125.98,
    size_y=85.85,
    size_z=23.24,
    lid=thermo_AB_96_wellplate_300ul_Vb_MicroAmp_Lid(name + "_lid") if with_lid else None,
    model=thermo_AB_96_wellplate_300ul_Vb_MicroAmp.__name__,
    plate_type="semi-skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.6,
      dy=8.59,
      dz=0.0,  # check that plate is semi-skirted
      item_dx=9,
      item_dy=9,
      size_x=5.494,
      size_y=5.494,
      size_z=23.24,
      bottom_type=WellBottomType.V,
      material_z_thickness=0.74,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=(
        _compute_volume_from_height_thermo_AB_96_wellplate_300ul_Vb_MicroAmp
      ),
      compute_height_from_volume=(
        _compute_height_from_volume_thermo_AB_96_wellplate_300ul_Vb_MicroAmp
      ),
    ),
  )


def thermo_AB_384_wellplate_40uL_Vb_MicroAmp(name: str) -> Plate:
  """Thermo Fisher Scientific cat. no.: 4309849, 4326270, 4343814 (with barcode), 4343370 (w/o barcode).

  https://documents.thermofisher.com/TFS-Assets/LSG/manuals/cms_042831.pdf
  """
  diameter = 3.17
  return Plate(
    name=name,
    size_x=127.8,
    size_y=85.5,
    size_z=9.70,
    lid=None,
    model=thermo_AB_384_wellplate_40uL_Vb_MicroAmp.__name__,
    plate_type="skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=12.15 - diameter / 2,
      dy=9 - diameter / 2,
      dz=0.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=diameter,
      size_y=diameter,
      size_z=9.70 - 0.61,
      bottom_type=WellBottomType.V,
      material_z_thickness=0.61,
      cross_section_type=CrossSectionType.CIRCLE,
    ),
  )


# # # # # # # # # # thermo_nunc_1_troughplate_90000uL_Fb_omnitray # # # # # # # # # #


def thermo_nunc_1_troughplate_90000uL_Fb_omnitray(name: str) -> Plate:
  """
  https://assets.fishersci.com/TFS-Assets/LSG/manuals/D03023.pdf

  - Brand: Thermo Scientific / Nunc
  - Part no.: 165218, 140156, 242811, 264728
  """

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.47,  # from spec
    size_z=14.5,  # from spec
    lid=None,  # TODO: define a matching Lid if you use one with this tray
    model=thermo_nunc_1_troughplate_90000uL_Fb_omnitray.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=(127.76 - 123.7) / 2,  # from spec
      dy=(85.47 - 81.3) / 2,  # from spec
      dz=14.5 - 11.7 - 2.5,  # from spec: plate_z - well_z - material_z_thickness
      item_dx=9.0,
      item_dy=9.0,
      size_x=123.7,  # from spec
      size_y=81.3,  # from spec
      size_z=11.7,  # from spec
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=2.5,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,
      # compute_volume_from_height=None,
      # compute_height_from_volume=None,
    ),
  )


# # # # # # # # # # Thermo_TS_Nunc_96_wellplate_300uL_Fb # # # # # # # # # #


def Thermo_TS_Nunc_96_wellplate_300uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """Thermo Scientific™ Nunc™ 96-Well Optical-Bottom Microplate, black, TC surface
  - Product Number: 165305
  - Max Volume: 400 uL
  - working volume: 50-300uL (in practice, although spec sheet says 50-200uL))
  - Manufacturer link: https://www.fishersci.com/shop/products/nunc-microwell-96-well-cell-culture-treated-flat-bottom-microplate/1256670#
  - Spec sheet info: https://documents.thermofisher.com/TFS-Assets/LCD/Schematics-%26-Diagrams/1653xx_0713.pdf
  """
  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.47,  # from spec
    size_z=14.86,  # from spec
    model="Thermo_TS_Nunc_96_wellplate_300uL_Fb",
    lid=Thermo_TS_Nunc_96_wellplate_300uL_Fb_Lid(name + "_lid") if with_lid else None,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      dx=11.095,  # from spec
      dy=8.025,  # from spec
      dz=1.98,  # from spec
      item_dx=9,  # from spec
      item_dy=9,  # from spec
      size_x=6.45,  # from spec
      size_y=6.45,  # from spec
      size_z=12.1,  # from spec
      bottom_type=WellBottomType.FLAT,  # flat bottom wells
      material_z_thickness=2.2,  # from spec
    ),
  )


def Thermo_TS_Nunc_96_wellplate_300uL_Fb_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.25,  # from spec
    size_y=85.3,  # from spec
    size_z=9.1,  # from spec
    nesting_z_height=16.7 - 14.86,  # from spec: lid+plate_z - plate_z
    model="Thermo_TS_Nunc_96_assay_300uL_Fb_Lid",
  )
