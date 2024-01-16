""" Porvair plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_square_vbottom


def _compute_volume_from_height_Porvair_6x47_Reservoir(h: float):
  if h > 42.5:
    raise ValueError(f"Height {h} is too large for Porvair_6x47_Reservoir")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=17,
    y=70.8,
    h_pyramid=5,
    h_cube=37.5,
    liquid_height=h)


#: Porvair_6x47_Reservoir
def Porvair_6x47_Reservoir(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=44,
    with_lid=with_lid,
    model="Porvair_6x47_Reservoir",
    lid_height=5,
    items=create_equally_spaced(Well,
      num_items_x=6,
      num_items_y=1,
      dx=9.3,
      dy=5.7,
      dz=2.24,
      item_dx= 18.5,
      item_dy=6.9,
      size_x=16.8,
      size_y=70.8,
      size_z=42.5,
      bottom_type=WellBottomType.V,
      compute_volume_from_height=_compute_volume_from_height_Porvair_6x47_Reservoir,
      cross_section_type=CrossSectionType.SQUARE
    ),
  )


#: Porvair_6x47_Reservoir_L
def Porvair_6x47_Reservoir_L(name: str, with_lid: bool = False) -> Plate:
  return Porvair_6x47_Reservoir(name=name, with_lid=with_lid)


#: Porvair_6x47_Reservoir_P
def Porvair_6x47_Reservoir_P(name: str, with_lid: bool = False) -> Plate:
  return Porvair_6x47_Reservoir(name=name, with_lid=with_lid).rotated(90)
