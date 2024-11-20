# https://cell-nest.oss-cn-zhangjiakou.aliyuncs.com/Resource/File/2022/11/24/NEST%20Reservoir.pdf

from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_rectangle,
  compute_volume_from_height_rectangle,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  Well,
  WellBottomType,
)


def nest_8_troughplate_22000uL_Vb(name: str) -> Plate:
  """part no 360101. not validated"""
  well_length = 8.2  # from datasheet
  well_width = 107.5  # from datasheet
  well_kwargs = {
    "size_x": well_width,
    "size_y": well_length,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_length, well_width=well_width
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_length, well_width=well_width
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
      **well_kwargs,
    ),
  )


def nest_12_troughplate_15000uL_Vb(name: str) -> Plate:
  """part no 360102."""
  well_length = 71.2  # from datasheet
  well_width = 8.2  # from datasheet
  well_kwargs = {
    "size_x": well_width,
    "size_y": well_length,
    "size_z": 26.85,  # from datasheet
    "bottom_type": WellBottomType.V,
    # an approximation: the trapezoid at the bottom is not fully defined in the datasheet
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume=liquid_volume, well_length=well_length, well_width=well_width
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height=liquid_height, well_length=well_length, well_width=well_width
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
      **well_kwargs,
    ),
  )
