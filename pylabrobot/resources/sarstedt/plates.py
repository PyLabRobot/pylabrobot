""" Sarstedt plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.plate import Plate

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_square_ubottom


def _compute_volume_from_height_Sarst_96_DW_2200UL(h: float):
  if h > 42.1:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return calculate_liquid_volume_container_2segments_square_ubottom(
    x=8.3,
    h_cuboid=37.7,
    liquid_height=h)


#: Axy_24_DW_10ML
def Sarst_96_DW_2200UL(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=44.0,
    with_lid=with_lid,
    model="Sarst_96_DW_2200UL",
    lid_height=5,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.88,
      dy=6.73,
      dz=2.15,
      item_dx=9,
      item_dy=9,
      size_x=8.3,
      size_y=8.3,
      size_z=41.85,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_Sarst_96_DW_2200UL,
      cross_section_type=CrossSectionType.SQUARE
    ),
  )


#: Axy_24_DW_10ML_L
def Sarst_96_DW_2200UL_L(name: str, with_lid: bool = False) -> Plate:
  return Sarst_96_DW_2200UL(name=name, with_lid=with_lid)


#: Axy_24_DW_10ML_P
def Sarst_96_DW_2200UL_P(name: str, with_lid: bool = False) -> Plate:
  return Sarst_96_DW_2200UL(name=name, with_lid=with_lid).rotated(90)
