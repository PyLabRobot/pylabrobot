""" Thermo Fisher & Thermo Fisher Scientific plates """

# pylint: disable=invalid-name

from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.plate import Plate

from pylabrobot.resources.volume_functions import calculate_liquid_volume_container_2segments_square_ubottom


def _compute_volume_from_height_ThermoScientific_96_1200ul_Rd(h: float):
  if h > 20.5:
    raise ValueError(f"Height {h} is too large for ThermoScientific_96_1200ul_Rd")
  return calculate_liquid_volume_container_2segments_square_ubottom(
    x=8.15,
    h_cuboid=16.45,
    liquid_height=h)


#: ThermoScientific_96_1200ul_Rd
def ThermoScientific_96_1200ul_Rd(name: str, with_lid: bool = False) -> Plate:
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=24.0,
    with_lid=with_lid,
    model="ThermoScientific_96_1200ul_Rd",
    lid_height=5,
    items=create_equally_spaced(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.6,
      dy=7.3,
      dz=0.2,
      item_dx=9,
      item_dy=9,
      size_x=8.3,
      size_y=8.3,
      size_z=20.5,
      bottom_type=WellBottomType.U,
      compute_volume_from_height=_compute_volume_from_height_ThermoScientific_96_1200ul_Rd,
      cross_section_type=CrossSectionType.RECTANGLE
    ),
  )


#: ThermoScientific_96_1200ul_Rd_L
def ThermoScientific_96_1200ul_Rd_L(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_1200ul_Rd(name=name, with_lid=with_lid)


#: ThermoScientific_96_1200ul_Rd_P
def ThermoScientific_96_1200ul_Rd_P(name: str, with_lid: bool = False) -> Plate:
  return ThermoScientific_96_1200ul_Rd(name=name, with_lid=with_lid).rotated(90)
