# https://cell-nest.oss-cn-zhangjiakou.aliyuncs.com/Resource/File/2022/11/24/NEST%20Reservoir.pdf

from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_rectangle,
  compute_volume_from_height_rectangle,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def nest_1_troughplate_195000uL_Vb(name: str) -> Plate:
  """part no 360103. 96 tiny holes, but one container."""
  well_size_x = 127.76 - (14.38 - 9 / 2) * 2  # from datasheet
  well_size_y = 85.48 - (11.24 - 9 / 2) * 2  # from datasheet
  well_kwargs = {
    "size_x": well_size_x,
    "size_y": well_size_y,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_size_x, well_width=well_size_y
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_size_x, well_width=well_size_y
    ),
    "material_z_thickness": 31.4 - 26.85 - 3.55,  # from datasheet
  }

  return Plate(
    name=name,
    size_x=127.76,  # from datasheet
    size_y=85.48,  # from datasheet
    size_z=31.4,  # from datasheet
    lid=None,
    model=nest_1_troughplate_195000uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=14.38 - 9 / 2,  # from datasheet
      dy=11.24 - 9 / 2,  # from datasheet
      dz=3.55,  # from datasheet
      item_dx=9.0,  # from datasheet
      item_dy=9.0,  # from datasheet
      name_prefix=name,
      **well_kwargs,
    ),
  )


def nest_1_troughplate_185000uL_Vb(name: str) -> Plate:
  """NEST single-trough reagent reservoir plate, 185 mL, V-bottom (ANSI/SLAS footprint).

  cat. no.: 360104

  384 tiny holes feed one shared container.
  """
  real_well_d = (85.48 - 8.99 * 2) / 15  # 4.5. in the drawing it says 2.4 which is wrong

  well_size_x = 127.76 - (12.13 - real_well_d / 2) * 2  # from datasheet
  well_size_y = 85.48 - (8.99 - real_well_d / 2) * 2  # from datasheet
  well_kwargs = {
    "size_x": well_size_x,
    "size_y": well_size_y,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    "cross_section_type": CrossSectionType.RECTANGLE,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_size_x, well_width=well_size_y
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_size_x, well_width=well_size_y
    ),
    "material_z_thickness": 31.4 - 26.85 - 3.55,  # from datasheet
  }

  return Plate(
    name=name,
    size_x=127.76,  # from datasheet
    size_y=85.48,  # from datasheet
    size_z=31.4,  # from datasheet
    lid=None,
    model=nest_1_troughplate_185000uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=12.13 - real_well_d / 2,  # from datasheet
      dy=8.99 - real_well_d / 2,  # from datasheet
      dz=3.55,  # from datasheet
      item_dx=9.0,  # from datasheet
      item_dy=9.0,  # from datasheet
      name_prefix=name,
      **well_kwargs,
    ),
  )


def nest_8_troughplate_22000uL_Vb(name: str) -> Plate:
  """NEST 8-trough reagent reservoir plate, 22 mL, V-bottom (ANSI/SLAS footprint).

  cat. no.: 360101

  Not validated.
  """
  well_size_x = 107.5  # from datasheet
  well_size_y = 8.2  # from datasheet
  well_kwargs = {
    "size_x": well_size_x,
    "size_y": well_size_y,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    "cross_section_type": CrossSectionType.RECTANGLE,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_size_x, well_width=well_size_y
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_size_x, well_width=well_size_y
    ),
    "material_z_thickness": 31.4 - 26.85 - 3.55,  # from datasheet
  }

  return Plate(
    name=name,
    size_x=127.76,  # from datasheet
    size_y=85.48,  # from datasheet
    size_z=31.4,  # from datasheet
    lid=None,
    model=nest_8_troughplate_22000uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=8,
      dx=(127.76 - 107.5) / 2,  # from datasheet
      dy=11.24 - 8.2 / 2,  # from datasheet
      dz=3.55,  # from datasheet
      item_dx=9.0,  # from datasheet
      item_dy=9.0,  # from datasheet
      name_prefix=name,
      **well_kwargs,
    ),
  )


def nest_12_troughplate_15000uL_Vb(name: str) -> Plate:
  """NEST 12-trough reagent reservoir plate, 15 mL, V-bottom (ANSI/SLAS footprint).

  cat. no.: 360102
  """
  well_size_x = 8.2  # from datasheet
  well_size_y = 71.2  # from datasheet
  well_kwargs = {
    "size_x": well_size_x,
    "size_y": well_size_y,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    "cross_section_type": CrossSectionType.RECTANGLE,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_size_x, well_width=well_size_y
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_size_x, well_width=well_size_y
    ),
    "material_z_thickness": 31.4 - 26.85 - 3.55,  # from datasheet
  }

  return Plate(
    name=name,
    size_x=127.76,  # from datasheet
    size_y=85.48,  # from datasheet
    size_z=31.4,  # from datasheet
    lid=None,
    model=nest_12_troughplate_15000uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=1,
      dx=14.38 - 8.2 / 2,  # from datasheet
      dy=(85.48 - 71.2) / 2,  # from datasheet
      dz=3.55,  # from datasheet
      item_dx=9.0,  # from datasheet
      item_dy=9.0,  # from datasheet
      name_prefix=name,
      **well_kwargs,
    ),
  )


def NEST_96_wellplate_2200uL_Ub(name: str) -> Plate:
  """NEST Cat. No. 503062
  Spec: http://manage-en.nestscientificusa.com/Resource/File/2023/02/13/NEST-Deep-Well-Plates.pdf
  """
  return Plate(
    name=name,
    size_x=127.1,  # from spec
    size_y=85.10,  # from spec
    size_z=41.85,  # from spec
    lid=None,
    model=NEST_96_wellplate_2200uL_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=8.0,  # from spec (inner well width)
      size_y=8.0,  # from spec (inner well length)
      size_z=39.30,  # from spec
      dx=10.05,  # from spec
      dy=7.05,  # from spec
      dz=2.55,  # from spec
      material_z_thickness=0.8,  # measured
      item_dx=9.0,  # from spec
      item_dy=9.0,  # from spec
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,
      bottom_type=WellBottomType.U,
      max_volume=2200,  # from spec (2.2 mL)
      name_prefix=name,
    ),
  )


# Liquid height over the cavity bottom (mm) -> volume (uL), computed from spec: the well is a
# truncated cone, 6.40 mm across the floor and 6.85 mm across the rim, 10.80 mm deep. The points
# are at about the nominal volume (200 uL) and at the brim.
_nest_96_wellplate_200uL_Fb_height_volume_data = {
  0.0: 0.0,
  6.0: 200.7,
  10.8: 372.4,
}


def nest_96_wellplate_200uL_Fb(name: str) -> Plate:
  """NEST Cat. No. 701011: 96 well cell culture plate, flat bottom, non-treated, polystyrene.
  Spec: NEST data sheet, "96 well cell culture plate, Flat bottom" technical drawing
  http://www.gzjetway.com/upload/pdf/202506/04a71256d0b04cf888df963be987194b.pdf
  Recommended medium volume: 0.1-0.2 mL, growth area: 0.32 cm2 per well
  https://www.nest-biotech.com/cell-culture-plates/59415537.html
  """
  return Plate(
    name=name,
    size_x=127.56,  # from spec
    size_y=85.36,  # from spec
    size_z=14.30,  # from spec
    lid=None,
    model=nest_96_wellplate_200uL_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=6.85,  # from spec (inner diameter at the rim)
      size_y=6.85,  # from spec (inner diameter at the rim)
      size_z=12.00,  # from spec (14.30 - 2.30)
      dx=10.855,  # from spec (A1 center 14.28 - 6.85 / 2)
      dy=7.755,  # from spec (row H center 11.18 from the front edge - 6.85 / 2)
      dz=2.30,  # from spec (3.50 cavity floor - 1.20 floor thickness)
      material_z_thickness=1.20,  # from spec
      item_dx=9.0,  # from spec
      item_dy=9.0,  # from spec
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      cross_section_type=CrossSectionType.CIRCLE,
      bottom_type=WellBottomType.FLAT,
      max_volume=372.4,  # computed from spec (brim-full, the last height_volume_data point)
      nominal_volume=200,  # from spec (recommended medium volume 0.1-0.2 mL)
      height_volume_data=_nest_96_wellplate_200uL_Fb_height_volume_data,
      name_prefix=name,
    ),
  )
