""" Corning Costar plates """

# pylint: disable=invalid-name
# pylint: disable=unreachable

from typing import Optional
from pylabrobot.resources.errors import ResourceDefinitionIncompleteError
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.well import Well, WellBottomType, CrossSectionType
from pylabrobot.resources.utils import create_ordered_items_2d

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_container_1segment_round_fbottom,
  calculate_liquid_height_in_container_2segments_square_vbottom,
  calculate_liquid_volume_container_1segment_round_fbottom,
  calculate_liquid_volume_container_2segments_square_vbottom,
  compute_height_from_volume_conical_frustum,
  compute_volume_from_height_conical_frustum
)


def _compute_volume_from_height_Cos_1536_10ul(h: float) -> float:
  volume = min(h, 0.25)*min(h, 0.25)*(2.9845 - 1.0472*min(h, 0.25))
  if h <= 5.75:
    volume += (h-0.25)*3.6100
  if h > 6.0:
    raise ValueError(f"Height {h} is too large for Cos_1536_10ul")
  return volume


def Cos_1536_10ul_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_1536_10ul_Lid",
  # )


def Cos_1536_10ul(name: str, with_lid: bool = False) -> Plate:
  """ Cos_1536_10ul """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_1536_10ul")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=10.25,
    lid=Cos_1536_10ul_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_1536_10ul",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=48,
      num_items_y=32,
      dx=9.675,
      dy=7.175,
      dz=0.5,
      item_dx=2.25,
      item_dy=2.25,
      size_x=1.9,
      size_y=1.9,
      size_z=5.75,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_1536_10ul,
    ),
  )

def Cos_1536_10ul_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_1536_10ul """
  return Cos_1536_10ul(name=name, with_lid=with_lid)

def Cos_1536_10ul_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_1536_10ul """
  return Cos_1536_10ul(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_384_DW(h: float) -> float:
  volume = min(h, 1.0)*min(h, 1.0)*(4.3982 - 1.0472*min(h, 1.0))
  if h <= 24.5:
    volume += (h-1.0)*10.0800
  if h > 25.5:
    raise ValueError(f"Height {h} is too large for Cos_384_DW")
  return volume


def Cos_384_DW_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_384_DW_Lid",
  # )


def Cos_384_DW(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_DW """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_384_DW")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=28.0,
    lid=Cos_384_DW_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_384_DW",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=7.85,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.6,
      size_y=2.8,
      size_z=24.5,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_DW,
    ),
  )

def Cos_384_DW_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_DW """
  return Cos_384_DW(name=name, with_lid=with_lid)

def Cos_384_DW_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_DW """
  return Cos_384_DW(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_384_PCR(h: float) -> float:
  volume = min(h, 9.5)*2.8510
  if h > 9.5:
    raise ValueError(f"Height {h} is too large for Cos_384_PCR")
  return volume


def Cos_384_PCR_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_384_PCR_Lid",
  # )


def Cos_384_PCR(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_PCR """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_384_PCR")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=16.0,
    lid=Cos_384_PCR_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_384_PCR",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.1,
      dy=7.6,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.3,
      size_y=3.3,
      size_z=9.5,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_PCR,
    ),
  )

def Cos_384_PCR_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_PCR """
  return Cos_384_PCR(name=name, with_lid=with_lid)

def Cos_384_PCR_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_PCR """
  return Cos_384_PCR(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_384_Sq(h: float) -> float:
  volume = min(h, 11.56)*12.2500
  if h > 11.56:
    raise ValueError(f"Height {h} is too large for Cos_384_Sq")
  return volume


def Cos_384_Sq_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_384_Sq_Lid",
  # )


def Cos_384_Sq(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_384_Sq")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    lid=Cos_384_Sq_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_384_Sq",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.0,
      dy=7.5,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.5,
      size_y=3.5,
      size_z=11.56,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq,
    ),
  )

def Cos_384_Sq_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq """
  return Cos_384_Sq(name=name, with_lid=with_lid)

def Cos_384_Sq_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq """
  return Cos_384_Sq(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_384_Sq_Rd(h: float) -> float:
  volume = min(h, 1.0)*min(h, 1.0)*(4.3982 - 1.0472*min(h, 1.0))
  if h <= 11.6:
    volume += (h-1.0)*10.0800
  if h > 12.6:
    raise ValueError(f"Height {h} is too large for Cos_384_Sq_Rd")
  return volume


def Cos_384_Sq_Rd_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_384_Sq_Rd_Lid",
  # )


def Cos_384_Sq_Rd(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq_Rd """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_384_Sq_Rd")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    lid=Cos_384_Sq_Rd_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_384_Sq_Rd",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=7.85,
      dz=1.0,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.6,
      size_y=2.8,
      size_z=11.6,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_384_Sq_Rd,
    ),
  )

def Cos_384_Sq_Rd_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq_Rd """
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid)

def Cos_384_Sq_Rd_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_384_Sq_Rd """
  return Cos_384_Sq_Rd(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_DW_1mL(h: float) -> float:
  volume = min(h, 2.5)*min(h, 2.5)*(10.2102 - 1.0472*min(h, 2.5))
  if h <= 40.0:
    volume += (h-2.5)*33.1831
  if h > 42.5:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_1mL")
  return volume


def Cos_96_DW_1mL_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_DW_1mL_Lid",
  # )


def Cos_96_DW_1mL(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_1mL """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_DW_1mL")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=42.0,
    lid=Cos_96_DW_1mL_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_DW_1mL",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.75,
      dy=8.25,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.5,
      size_y=6.5,
      size_z=40.0,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_1mL,
    ),
  )

def Cos_96_DW_1mL_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_1mL """
  return Cos_96_DW_1mL(name=name, with_lid=with_lid)

def Cos_96_DW_1mL_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_1mL """
  return Cos_96_DW_1mL(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_DW_2mL(h: float) -> float:
  volume = min(h, 4.0)*min(h, 4.0)*(12.5664 - 1.0472*min(h, 4.0))
  if h <= 42.0:
    volume += (h-4.0)*64.0000
  if h > 46.0:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_2mL")
  return volume


def Cos_96_DW_2mL_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_DW_2mL_Lid",
  # )


def Cos_96_DW_2mL(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_2mL """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_DW_2mL")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    lid=Cos_96_DW_2mL_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_DW_2mL",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=7.5,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.0,
      size_y=8.0,
      size_z=42.0,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_2mL,
    ),
  )

def Cos_96_DW_2mL_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_2mL """
  return Cos_96_DW_2mL(name=name, with_lid=with_lid)

def Cos_96_DW_2mL_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_2mL """
  return Cos_96_DW_2mL(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_DW_500ul(h: float) -> float:
  volume = min(h, 1.5)*10.7233
  if h <= 25.0:
    volume += (h-1.5)*34.7486
  if h > 26.5:
    raise ValueError(f"Height {h} is too large for Cos_96_DW_500ul")
  return volume


def Cos_96_DW_500ul_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_DW_500ul_Lid",
  # )


def Cos_96_DW_500ul(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_500ul """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_DW_500ul")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=27.5,
    lid=Cos_96_DW_500ul_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_DW_500ul",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=2.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=25.0,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_DW_500ul,
    ),
  )

def Cos_96_DW_500ul_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_500ul """
  return Cos_96_DW_500ul(name=name, with_lid=with_lid)

def Cos_96_DW_500ul_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_DW_500ul """
  return Cos_96_DW_500ul(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_EZWash(h: float) -> float:
  volume = min(h, 11.3)*37.3928
  if h > 11.3:
    raise ValueError(f"Height {h} is too large for Cos_96_EZWash")
  return volume


def Cos_96_EZWash_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_EZWash_Lid",
  # )


def Cos_96_EZWash(name: str, with_lid: bool = False) -> Plate:
  raise ValueError("Deprecated. You probably want to use Cor_96_wellplate_360ul_Fb instead.")

def Cos_96_EZWash_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_EZWash """
  return Cos_96_EZWash(name=name, with_lid=with_lid)

def Cos_96_EZWash_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_EZWash """
  return Cos_96_EZWash(name=name, with_lid=with_lid).rotated(z=90)


def _compute_volume_from_height_Cos_96_FL(h: float) -> float:
  volume = min(h, 10.67)*34.2808
  if h > 10.67:
    raise ValueError(f"Height {h} is too large for Cos_96_FL")
  return volume


def Cos_96_FL_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_FL_Lid",
  # )


def Cos_96_FL(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_FL """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_FL")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    lid=Cos_96_FL_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_FL",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.57,
      dy=8.07,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.86,
      size_y=6.86,
      size_z=10.67,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_FL,
    ),
  )

def _compute_volume_from_height_Cos_96_Filter(h: float) -> float:
  volume = min(h, 12.2)*34.7486
  if h > 12.2:
    raise ValueError(f"Height {h} is too large for Cos_96_Filter")
  return volume


def Cos_96_Filter_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_Filter_Lid",
  # )


def Cos_96_Filter(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Filter """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_Filter")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    lid=Cos_96_Filter_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_Filter",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=2.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=12.2,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Filter,
    ),
  )

def Cos_96_Filter_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Filter """
  return Cos_96_Filter(name=name, with_lid=with_lid)

def Cos_96_Filter_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Filter """
  return Cos_96_Filter(name=name, with_lid=with_lid).rotated(z=90)

def Cos_96_Fl_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Fl """
  return Cos_96_FL(name=name, with_lid=with_lid)

def Cos_96_Fl_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Fl """
  return Cos_96_FL(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_HalfArea(h: float) -> float:
  volume = min(h, 10.7)*17.7369
  if h > 10.7:
    raise ValueError(f"Height {h} is too large for Cos_96_HalfArea")
  return volume


def Cos_96_HalfArea_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_HalfArea_Lid",
  # )


def Cos_96_HalfArea(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_HalfArea """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_HalfArea")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    lid=Cos_96_HalfArea_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_HalfArea",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.5,
      dy=9.0,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=5.0,
      size_y=5.0,
      size_z=10.7,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_HalfArea,
    ),
  )

def Cos_96_HalfArea_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_HalfArea """
  return Cos_96_HalfArea(name=name, with_lid=with_lid)

def Cos_96_HalfArea_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_HalfArea """
  return Cos_96_HalfArea(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_PCR(h: float) -> float:
  volume = min(h, 11.5)*6.5450
  if h <= 20.5:
    volume += (h-11.5)*23.8237
  if h > 32.0:
    raise ValueError(f"Height {h} is too large for Cos_96_PCR")
  return volume


def Cos_96_PCR_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_PCR_Lid",
  # )


def Cos_96_PCR(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_PCR """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_PCR")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=22.5,
    lid=Cos_96_PCR_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_PCR",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.0,
      dy=8.5,
      dz=0.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.0,
      size_y=6.0,
      size_z=20.5,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_PCR,
    ),
  )

def Cos_96_PCR_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_PCR """
  return Cos_96_PCR(name=name, with_lid=with_lid)

def Cos_96_PCR_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_PCR """
  return Cos_96_PCR(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_ProtCryst(h: float) -> float:
  volume = min(h, 1.6)*7.5477
  if h > 1.6:
    raise ValueError(f"Height {h} is too large for Cos_96_ProtCryst")
  return volume


def Cos_96_ProtCryst_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_ProtCryst_Lid",
  # )


def Cos_96_ProtCryst(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_ProtCryst """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_ProtCryst")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=20.0,
    lid=Cos_96_ProtCryst_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_ProtCryst",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=24,
      num_items_y=8,
      dx=10.15,
      dy=9.95,
      dz=10.0,
      item_dx=4.5,
      item_dy=9.0,
      size_x=3.1,
      size_y=3.1,
      size_z=1.6,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_ProtCryst,
    ),
  )

def Cos_96_ProtCryst_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_ProtCryst """
  return Cos_96_ProtCryst(name=name, with_lid=with_lid)

def Cos_96_ProtCryst_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_ProtCryst """
  return Cos_96_ProtCryst(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_Rd(h: float) -> float:
  volume = min(h, 0.6)*min(h, 0.6)*(10.0531 - 1.0472*min(h, 0.6))
  if h <= 11.3:
    volume += (h-0.6)*34.7486
  if h > 11.9:
    raise ValueError(f"Height {h} is too large for Cos_96_Rd")
  return volume


def Cos_96_Rd_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_Rd_Lid",
  # )


def Cos_96_Rd(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Rd """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_Rd")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.5,
    lid=Cos_96_Rd_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_Rd",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=0.75,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=11.3,
      bottom_type=WellBottomType.U,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Rd,
    ),
  )

def Cos_96_Rd_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Rd """
  return Cos_96_Rd(name=name, with_lid=with_lid)

def Cos_96_Rd_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Rd """
  return Cos_96_Rd(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_SpecOps(h: float) -> float:
  volume = min(h, 11.0)*34.7486
  if h > 11.0:
    raise ValueError(f"Height {h} is too large for Cos_96_SpecOps")
  return volume


def Cos_96_SpecOps_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_SpecOps_Lid",
  # )


def Cos_96_SpecOps(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_SpecOps """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_SpecOps")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    lid=Cos_96_SpecOps_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_SpecOps",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=0.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=11.0,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_SpecOps,
    ),
  )

def Cos_96_SpecOps_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_SpecOps """
  return Cos_96_SpecOps(name=name, with_lid=with_lid)

def Cos_96_SpecOps_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_SpecOps """
  return Cos_96_SpecOps(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_UV(h: float) -> float:
  volume = min(h, 11.0)*34.7486
  if h > 11.0:
    raise ValueError(f"Height {h} is too large for Cos_96_UV")
  return volume


def Cos_96_UV_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_UV_Lid",
  # )


def Cos_96_UV(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_UV """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_UV")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.3,
    lid=Cos_96_UV_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_UV",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=11.0,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_UV,
    ),
  )

def Cos_96_UV_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_UV """
  return Cos_96_UV(name=name, with_lid=with_lid)

def Cos_96_UV_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_UV """
  return Cos_96_UV(name=name, with_lid=with_lid).rotated(z=90)

def _compute_volume_from_height_Cos_96_Vb(h: float) -> float:
  volume = min(h, 1.4)*10.5564
  if h <= 10.9:
    volume += (h-1.4)*36.9605
  if h > 12.3:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return volume


def Cos_96_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_Vb_Lid",
  # )


def Cos_96_Vb(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Vb """

  raise ResourceDefinitionIncompleteError(resource_name="Cos_96_Vb")

  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=14.24,
    lid=Cos_96_Vb_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_Vb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.55,
      dy=8.05,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.9,
      size_y=6.9,
      size_z=10.9,
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_Vb,
    ),
  )

def Cos_96_Vb_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Vb """
  return Cos_96_Vb(name=name, with_lid=with_lid)

def Cos_96_Vb_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_Vb """
  return Cos_96_Vb(name=name, with_lid=with_lid).rotated(z=90)


############ User-defined PLR Cos plates ############


# # # # # # # # # # Cos_6_wellplate_16800ul_Fb # # # # # # # # # #

def Cos_6_wellplate_16800ul_Fb_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=7.8,
    nesting_z_height=6.7, # measure overlap between lid and plate
    model="Cos_6_wellplate_16800ul_Fb_Lid",
  )

def _compute_volume_from_height_Cos_6_wellplate_16800ul_Fb(h: float):
  if h > 18.0:
    raise ValueError(f"Height {h} is too large for Cos_6_wellplate_16800ul_Fb")
  return calculate_liquid_volume_container_1segment_round_fbottom(
    d=35.0,
    h_cylinder=18.2,
    liquid_height=h)

def _compute_height_from_volume_Cos_6_wellplate_16800ul_Fb(liquid_volume: float):
  if liquid_volume > 17_640: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for Cos_6_wellplate_16800ul_Fb")
  return calculate_liquid_height_container_1segment_round_fbottom(
    d=35.0,
    h_cylinder=18.2,
    liquid_volume=liquid_volume)

def Cos_6_wellplate_16800ul_Fb(name: str, with_lid: bool = True) -> Plate:
  """Corning-Costar 6-well multi-well plate (MWP); product no.: 3516.
  - Material: ?
  - Cleanliness: 3516: sterilized by gamma irradiation
  - Nonreversible lids with condensation rings to reduce contamination
  - Treated for optimal cell attachment
  - Cell growth area: 9.5 cm² (approx.)
  - Total volume: 16.8 mL
  """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=20.0,
    lid=Cos_6_wellplate_16800ul_Fb_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_6_wellplate_16800ul_Fb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=3,
      num_items_y=2,
      dx=7.0,
      dy=5.45,
      dz=1.35,
      item_dx=38.45,
      item_dy=38.45,
      size_x=35.0,
      size_y=35.0,
      size_z=17.5,
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=1.4,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_6_wellplate_16800ul_Fb,
      compute_height_from_volume=_compute_height_from_volume_Cos_6_wellplate_16800ul_Fb,
    ),
  )

def Cos_6_wellplate_16800ul_Fb_L(name: str, with_lid: bool = True) -> Plate:
  return Cos_6_wellplate_16800ul_Fb(name=name, with_lid=with_lid)

def Cos_6_wellplate_16800ul_Fb_P(name: str, with_lid: bool = True) -> Plate:
  return Cos_6_wellplate_16800ul_Fb(name=name, with_lid=with_lid).rotated(z=90)


# # # # # # # # # # Cos_96_wellplate_2mL_Vb # # # # # # # # # #

def _compute_volume_from_height_Cos_96_wellplate_2mL_Vb(h: float) -> float:
  if h > 44.1: # 5% tolerance
    raise ValueError(f"Height {h} is too large for Cos_96_DWP_2mL_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=7.8,
    y=7.8,
    h_pyramid=4.0,
    h_cube=38.0,
    liquid_height=h)

def _compute_height_from_volume_Cos_96_wellplate_2mL_Vb(liquid_volume: float):
  if liquid_volume > 2_100: # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for Cos_96_wellpate_2mL_Vb")
  return round(calculate_liquid_height_in_container_2segments_square_vbottom(
    x=7.8,
    y=7.8,
    h_pyramid=4.0,
    h_cube=38.0,
    liquid_volume=liquid_volume),3)

def Cos_96_wellplate_2mL_Vb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")
  # See https://github.com/PyLabRobot/pylabrobot/pull/161.
  # return Lid(
  #   name=name,
  #   size_x=127.0,
  #   size_y=86.0,
  #   size_z=None,           # measure the total z height
  #   nesting_z_height=None, # measure overlap between lid and plate
  #   model="Cos_96_wellplate_2mL_Vb_Lid",
  # )

def Cos_96_DWP_2mL_Vb(name: str, with_lid: bool = False) -> Plate:
  raise NotImplementedError("This function is deprecated and will be removed in a future version."
          " Use 'Cos_96_wellplate_2mL_Vb' instead.")

def Cos_96_wellplate_2mL_Vb(name: str, with_lid: bool = False) -> Plate:
  """ Corning 96 deep-well 2 mL PCR plate. Corning cat. no.: 3960
  - Material: Polypropylene
  - Resistant to many common organic solvents (e.g., DMSO, ethanol, methanol)
  - 3960: Sterile and DNase- and RNase-free
  - Total volume: 2 mL
  - Features uniform skirt heights for greater robotic gripping surface
  """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    lid=Cos_96_wellplate_2mL_Vb_Lid(name=name + "_lid") if with_lid else None,
    model="Cos_96_wellplate_2mL_Vb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.6,
      dy=7.0,
      dz=1.2,
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.0,
      size_y=8.0,
      size_z=42.0,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.25,
      cross_section_type=CrossSectionType.RECTANGLE,
      compute_volume_from_height=_compute_volume_from_height_Cos_96_wellplate_2mL_Vb,
      compute_height_from_volume=_compute_height_from_volume_Cos_96_wellplate_2mL_Vb
    ),
  )

def Cos_96_wellplate_2mL_Vb_L(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_wellplate_2mL_Vb """
  return Cos_96_wellplate_2mL_Vb(name=name, with_lid=with_lid)

def Cos_96_wellplate_2mL_Vb_P(name: str, with_lid: bool = False) -> Plate:
  """ Cos_96_wellplate_2mL_Vb """
  return Cos_96_wellplate_2mL_Vb(name=name, with_lid=with_lid).rotated(z=90)


# # # # # # # # # # Cor_96_wellplate_360ul_Fb # # # # # # # # # #

def Cor_96_wellplate_360ul_Fb_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=8.9,             # measure the total z height
    nesting_z_height=7.6,   # measure overlap between lid and plate
    model="Cor_96_wellplate_360ul_Fb_Lid",
  )


def Cor_96_wellplate_360ul_Fb(name: str, with_lid: bool = False) -> Plate:
  """ Cor_96_wellplate_360ul_Fb

  Catalog number 3603

  https://ecatalog.corning.com/life-sciences/b2b/NL/en/Microplates/Assay-Microplates/96-Well-
  Microplates/Corning®-96-well-Black-Clear-and-White-Clear-Bottom-Polystyrene-Microplates/p/3603

  Measurements found here:
  https://www.corning.com/catalog/cls/documents/drawings/MicroplateDimensions96-384-1536.pdf
  https://archive.vn/CnRgl
  """

  # This used to be Cos_96_EZWash in the Esvelt lab
  #
  # return Plate(
  #   name=name,
  #   size_x=127,
  #   size_y=86,
  #   size_z=14.5,
  #   lid=Cos_96_EZWash_Lid(name=name + "_lid") if with_lid else None,
  #   model="Cos_96_EZWash",
  #   ordered_items=create_ordered_items_2d(Well,
  #     num_items_x=12,
  #     num_items_y=8,
  #     dx=10.55,
  #     dy=8.05,
  #     dz=1.0,
  #     item_dx=9.0,
  #     item_dy=9.0,
  #     size_x=6.9,
  #     size_y=6.9,
  #     size_z=11.3,
  #     bottom_type=WellBottomType.FLAT,
  #     cross_section_type=CrossSectionType.CIRCLE,
  #   ),
  # )

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.2,
    lid=Cor_96_wellplate_360ul_Fb_Lid(name=name + "_lid") if with_lid else None,
    model="Cor_96_wellplate_360ul_Fb",
    ordered_items=create_ordered_items_2d(Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.87, # 14.3-6.86/2
      dy=7.77, # 11.2-6.86/2
      dz=3.03,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.86, # top
      size_y=6.86, # top
      size_z=10.67,
      material_z_thickness=0.5,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=360,
    ),
  )

def Cor_6_wellplate_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.: 3471
  - Material: Polystyrene
  - Tissue culture treated: No
  """
  BOTTOM_INNER_WELL_RADIUS = 34.798 / 2  # from Corning Product Description
  TOP_INNER_WELL_RADIUS = 35.433 / 2  # from Corning Product Description

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 17.399,  # from Corning Product Description
    "bottom_type": WellBottomType.FLAT,
    "max_volume": 16.850,  # calculated
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
  }

  return Plate(
    name=name,
    size_x=127.762,  # from Corning Product Description
    size_y=85.471,   # from Corning Product Description
    size_z=22.6314,  # from Corning Product Description
    lid=lid,
    model=Cor_6_wellplate_Fl.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=7.5,  # measured
      dy=6.3,  # measured
      dz=1.8,  # calibrated manually by z-stepping down using a pipette.
      item_dx=39,  # measured
      item_dy=39,  # measured
      **well_kwargs,
    ),
  )
