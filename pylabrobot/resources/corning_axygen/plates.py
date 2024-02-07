""" Corning Axygen plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.plate import Plate

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_square_vbottom


def _compute_volume_from_height_Axy_24_DW_10ML(h: float):
  if h > 42.1:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=17,
    y=17,
    h_pyramid=5,
    h_cube=37,
    liquid_height=h)


#: Axy_24_DW_10ML
def Axy_24_DW_10ML(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=44.24,
    with_lid=with_lid,
    model="Axy_24_DW_10ML",
    lid_height=5,
    items=create_equally_spaced(Well,
      num_items_x=6,
      num_items_y=4,
      dx=9.8,
      dy=7.2,
      dz=1.2,
      item_dx= 18,
      item_dy=18,
      size_x=17.0,
      size_y=17.0,
      size_z=42,
      bottom_type=WellBottomType.V,
      compute_volume_from_height=_compute_volume_from_height_Axy_24_DW_10ML,
      cross_section_type=CrossSectionType.SQUARE
    ),
  )


#: Axy_24_DW_10ML_L
def Axy_24_DW_10ML_L(name: str, with_lid: bool = False) -> Plate:
  return Axy_24_DW_10ML(name=name, with_lid=with_lid)


#: Axy_24_DW_10ML_P
def Axy_24_DW_10ML_P(name: str, with_lid: bool = False) -> Plate:
  return Axy_24_DW_10ML(name=name, with_lid=with_lid).rotated(90)
