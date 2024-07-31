""" Limbro plates """

# pylint: disable=invalid-name

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d


def _compute_volume_from_height_Limbro_24_Large(h: float) -> float:
  volume = min(h, 13.0)*226.9801
  if h > 13.0:
    raise ValueError(f"Height {h} is too large for Limbro_24_Large")
  return volume


def Limbro_24_Large_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Limbro_24_Large_Lid",
  # )


def Limbro_24_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_24_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    lid=Limbro_24_Large_Lid(name + "_lid") if with_lid else None,
    model="Limbro_24_Large",
    ordered_items=create_ordered_items_2d(Well,
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


def Limbro_24_Small_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Limbro_24_Small_Lid",
  # )


def Limbro_24_Small(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_24_Small """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    lid=Limbro_24_Small_Lid(name + "_lid") if with_lid else None,
    model="Limbro_24_Small",
    ordered_items=create_ordered_items_2d(Well,
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


def Limbro_48_Large_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Limbro_48_Large_Lid",
  # )


def Limbro_48_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_48_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    lid=Limbro_48_Large_Lid(name + "_lid") if with_lid else None,
    model="Limbro_48_Large",
    ordered_items=create_ordered_items_2d(Well,
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


def Limbro_96_Large_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Limbro_96_Large_Lid",
  # )


def Limbro_96_Large(name: str, with_lid: bool = False) -> Plate:
  """ Limbro_96_Large """
  return Plate(
    name=name,
    size_x=109.0,
    size_y=152.0,
    size_z=25.0,
    lid=Limbro_96_Large_Lid(name + "_lid") if with_lid else None,
    model="Limbro_96_Large",
    ordered_items=create_ordered_items_2d(Well,
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
