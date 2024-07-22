""" Porvair plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_equally_spaced_2d

from pylabrobot.resources.height_volume_functions import calculate_liquid_volume_container_2segments_round_vbottom


def _compute_volume_from_height_FrameStar_96_wellplate_skirted(h: float):
  if h > 42.5:
    raise ValueError(f"Height {h} is too large for Azenta4titudeFrameStar_96_wellplate_skirted")
  return calculate_liquid_volume_container_2segments_round_vbottom(
    d=5.5,
    h_cone=9.8,
    h_cylinder=5.2,
    liquid_height=h)

def Azenta4titudeFrameStar_96_wellplate_skirted_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.76,
  #   size_y=85.48,
  #   size_z=5,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Azenta4titudeFrameStar_96_wellplate_skirted_Lid",
  # )

#: Azenta4titudeFrameStar_96_wellplate_skirted
def Azenta4titudeFrameStar_96_wellplate_skirted(name: str, with_lid: bool = False) -> Plate:
  # https://www.azenta.com/products/framestar-96-well-skirted-pcr-plate#specifications
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=16,
    lid=Azenta4titudeFrameStar_96_wellplate_skirted_Lid(name + "_lid") if with_lid else None,
    model="Azenta4titudeFrameStar_96_wellplate_skirted",
    items=create_equally_spaced_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.5,
      dy=8.0,
      dz=1.54,
      item_dx=9,
      item_dy=9,
      size_x=5.5,
      size_y=5.5,
      size_z=15,
      bottom_type=WellBottomType.V,
      compute_volume_from_height=_compute_volume_from_height_FrameStar_96_wellplate_skirted,
      cross_section_type=CrossSectionType.CIRCLE
    ),
  )


#: Azenta4titudeFrameStar_96_wellplate_skirted_L
def Azenta4titudeFrameStar_96_wellplate_skirted_L(name: str, with_lid: bool = False) -> Plate:
  return Azenta4titudeFrameStar_96_wellplate_skirted(name=name, with_lid=with_lid)


#: Azenta4titudeFrameStar_96_wellplate_skirted_P
def Azenta4titudeFrameStar_96_wellplate_skirted_P(name: str, with_lid: bool = False) -> Plate:
  return Azenta4titudeFrameStar_96_wellplate_skirted(name=name, with_lid=with_lid).rotated(90)
