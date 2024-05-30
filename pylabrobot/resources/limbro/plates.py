""" Limbro plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.itemized_resource import create_equally_spaced


def _compute_volume_from_height_Limbro_24_Large(h: float) -> float:
  volume = min(h, 13.0)*226.9801
  if h > 13.0:
    raise ValueError(f"Height {h} is too large for Limbro_24_Large")
  return volume


def Limbro_24_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_24_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    with_lid=with_lid,
    model="Limbro_24_Large",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=4,
      num_items_y=6,
      dx=6.0,
      dy=11.5,
      dz=1.0,
      item_dx=22.4,
      item_dy=22.4,
      size_x=17.0,
      size_y=17.0,
      size_z=13.0,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Limbro_24_Large,
    ),
  )

def _compute_volume_from_height_Limbro_24_Small(h: float) -> float:
  volume = min(h, 1.5)*min(h, 1.5)*(7.8540 - 1.0472*min(h, 1.5))
  if h > 1.5:
    raise ValueError(f"Height {h} is too large for Limbro_24_Small")
  return volume


def Limbro_24_Small(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_24_Small """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    with_lid=with_lid,
    model="Limbro_24_Small",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=4,
      num_items_y=6,
      dx=17.5,
      dy=17.5,
      dz=1.0,
      item_dx=22.4,
      item_dy=22.4,
      size_x=5.0,
      size_y=5.0,
      size_z=1.5,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Limbro_24_Small,
    ),
  )

def _compute_volume_from_height_Limbro_48_Large(h: float) -> float:
  volume = min(h, 13.0)*113.0973
  if h > 13.0:
    raise ValueError(f"Height {h} is too large for Limbro_48_Large")
  return volume


def Limbro_48_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_48_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    with_lid=with_lid,
    model="Limbro_48_Large",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=4,
      num_items_y=12,
      dx=16.0,
      dy=8.0,
      dz=1.0,
      item_dx=22.4,
      item_dy=11.2,
      size_x=12.0,
      size_y=12.0,
      size_z=13.0,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Limbro_48_Large,
    ),
  )

def _compute_volume_from_height_Limbro_96_Large(h: float) -> float:
  volume = min(h, 13.0)*113.0973
  if h > 13.0:
    raise ValueError(f"Height {h} is too large for Limbro_96_Large")
  return volume


def Limbro_96_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_96_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    with_lid=with_lid,
    model="Limbro_96_Large",
    lid_height=10,
    items=create_equally_spaced(Well,
      num_items_x=8,
      num_items_y=12,
      dx=9.0,
      dy=9.0,
      dz=1.0,
      item_dx=11.2,
      item_dy=11.2,
      size_x=12.0,
      size_y=12.0,
      size_z=13.0,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Limbro_96_Large,
    ),
  )
