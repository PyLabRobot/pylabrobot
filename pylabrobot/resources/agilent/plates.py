from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_container_1segment_round_vbottom,
  calculate_liquid_volume_container_1segment_round_vbottom,
)


def _compute_volume_from_height_agilent_96_wellplate_150uL_Vb(
  h: float,
):
  # well depth: 12.5 mm
  # well diameter at the top: 6.0 mm
  if h > 12.5:
    raise ValueError(f"Height {h} is too large for agilent_96_wellplate_150uL_Vb")
  return calculate_liquid_volume_container_1segment_round_vbottom(
    d=6.4, h_pyramid=12.5, liquid_height=h
  )


def _compute_height_from_volume_agilent_96_wellplate_150uL_Vb(
  v: float,
):
  if v > 150:
    raise ValueError(f"Volume {v} is too large for agilent_96_wellplate_150uL_Vb")
  return calculate_liquid_height_container_1segment_round_vbottom(
    d=6.4, h_pyramid=12.5, liquid_volume=v
  )


def agilent_96_wellplate_150uL_Ub(name: str) -> Plate:
  """
  Part number: 5042-8502
  """

  diameter = 6.4  # from spec

  well_kwargs = {
    "size_x": diameter,  # from spec
    "size_y": diameter,  # from spec
    "size_z": 14.0,  # from spec
    "bottom_type": WellBottomType.U,
    "material_z_thickness": 0.88,  # measured using z-probing
    "max_volume": 150,
  }

  return Plate(
    name=name,
    size_x=127.8,  # standard
    size_y=85.5,  # standard
    size_z=15.9,  # from spec
    lid=None,
    model=agilent_96_wellplate_150uL_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - diameter / 2,  # from spec
      dy=11.2 - diameter / 2,  # from spec
      dz=16.0 - 14.0 - 0.88,  # spec - spec - measured
      item_dx=9.0,  # standard
      item_dy=9.0,  # standard
      **well_kwargs,
    ),
    plate_type="skirted",
  )


def agilent_96_wellplate_150uL_Vb(name: str) -> Plate:
  """
  Part number: 5042-8502
  """

  diameter = 6.4  # from spec

  well_kwargs = {
    "size_x": diameter,  # from spec
    "size_y": diameter,  # from spec
    "size_z": 14.0,  # from spec
    "bottom_type": WellBottomType.V,
    "material_z_thickness": 0.88,  # measured using z-probing
    "max_volume": 150,
    "compute_volume_from_height": _compute_volume_from_height_agilent_96_wellplate_150uL_Vb,
    "compute_height_from_volume": _compute_height_from_volume_agilent_96_wellplate_150uL_Vb,
  }

  return Plate(
    name=name,
    size_x=127.8,  # standard
    size_y=85.5,  # standard
    size_z=15.9,  # from spec
    lid=None,
    model=agilent_96_wellplate_150uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=13.4 - diameter / 2,  # measured
      dy=11.2 - diameter / 2,  # from spec
      dz=16.0 - 14.0 + 1,  # spec - spec - measured
      item_dx=9.0,  # standard
      item_dy=9.0,  # standard
      **well_kwargs,
    ),
    plate_type="skirted",
  )
