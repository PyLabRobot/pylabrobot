"""Corning plates."""

import warnings

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_in_container_2segments_square_vbottom,
  calculate_liquid_volume_container_2segments_square_vbottom,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # cor_96_wellplate_360uL_Fb # # # # # # # # # #

# Well tapers from 6.35 mm (bottom) to 6.86 mm (top) over 10.67 mm depth.
_cor_96_wellplate_360uL_Fb_height_volume_data = {
  0.0: 0.0,
  0.45: 20.0,  # "dead volume" to cover the full flat bottom and ensure LLD detectability
  1.69: 50.0,
  3.22: 100.0,
  4.72: 150.0,
  6.19: 200.0,
  7.72: 250.0,
  8.99: 300.0,
  10.62: 350.0,
  10.95: 360.0,
}


def cor_96_wellplate_360uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """
  Corning cat. no.s: 3603
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/
    Assay-Microplates/96-Well-Microplates/Corning%C2%AE-96-well-Black-Clear-and-White-Clear-
    Bottom-Polystyrene-Microplates/p/3603
  - brand: Corning
  - distributor: (Fisher Scientific, 10530753)
  - material: Polypropylene
  - tc_treated: Yes
  - tech_drawing: tech_drawings/Cor_96_wellplate_360ul_Fb.pdf
  - sterile: yes
  - notes:
      - Well bottom (i.e. material_z_thickness) is 60% thinner than conventional polystyrene
        microplates, resulting in lower background fluorescence & enabling readings down to 340 nm.
      - Opaque walls prevent well-to-well cross-talk.
  """

  # This used to be Cos_96_EZWash in the Esvelt lab

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.2,
    lid=cor_96_wellplate_360uL_Fb_lid(name=name + "_lid") if with_lid else None,
    model=cor_96_wellplate_360uL_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.87,  # 14.3-6.86/2
      dy=7.77,  # 11.2-6.86/2
      dz=3.03,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.86,  # top
      size_y=6.86,  # top
      size_z=10.67,
      material_z_thickness=0.5,
      bottom_type=WellBottomType.FLAT,
      cross_section_type=CrossSectionType.CIRCLE,
      max_volume=360,
      height_volume_data=_cor_96_wellplate_360uL_Fb_height_volume_data,
    ),
  )


def cor_96_wellplate_360uL_Fb_lid(name: str) -> Lid:
  """
  - brand: Corning
  """

  return Lid(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=8.9,  # measure the total z height
    nesting_z_height=7.6,  # measure overlap between lid and plate
    model=cor_96_wellplate_360uL_Fb_lid.__name__,
  )


# # # # # # # # # # cor_96_wellplate_2mL_Vb # # # # # # # # # #


def cor_96_wellplate_2mL_Vb(name: str) -> Plate:
  """
  Corning cat. no.: 3960
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Genomics-%26-
    Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Corning%C2%AE-96-well-
    Polypropylene-Storage-Blocks/p/3960
  - brand: Corning
  - distributor: (Fisher Scientific, 10708212)
  - material: Polypropylene
  - sterile: yes
  - notes:
      - features uniform skirt heights for greater robotic gripping surface.
  """
  return Plate(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=43.5,
    lid=None,
    model=cor_96_wellplate_2mL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
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
      compute_volume_from_height=_compute_volume_from_height_Cor_96_wellplate_2mL_Vb,
      compute_height_from_volume=_compute_height_from_volume_Cor_96_wellplate_2mL_Vb,
    ),
  )


# Volume-height functions
def _compute_volume_from_height_Cor_96_wellplate_2mL_Vb(
  h: float,
) -> float:
  if h > 44.1:  # 5% tolerance
    raise ValueError(f"Height {h} is too large for Cor_96_wellplate_2mL_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=7.8, y=7.8, h_pyramid=4.0, h_cube=38.0, liquid_height=h
  )


def _compute_height_from_volume_Cor_96_wellplate_2mL_Vb(
  liquid_volume: float,
):
  if liquid_volume > 2_100:  # 5% tolerance
    raise ValueError(f"Volume {liquid_volume} is too large for Cor_96_wellplate_2mL_Vb")
  return round(
    calculate_liquid_height_in_container_2segments_square_vbottom(
      x=7.8,
      y=7.8,
      h_pyramid=4.0,
      h_cube=38.0,
      liquid_volume=liquid_volume,
    ),
    3,
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Cor_96_wellplate_2mL_Vb(name: str, with_lid: bool = False) -> Plate:  # remove 2026-10
  """Deprecated alias for cor_96_wellplate_2mL_Vb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_96_wellplate_2mL_Vb()` instead.
  """
  warnings.warn(
    "Cor_96_wellplate_2mL_Vb() is deprecated and will be removed after 2026-10. "
    "Use cor_96_wellplate_2mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_96_wellplate_2mL_Vb(name)


def Cor_96_wellplate_360ul_Fb(name: str, with_lid: bool = False) -> Plate:  # remove 2026-10
  """Deprecated alias for cor_96_wellplate_360uL_Fb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_96_wellplate_360uL_Fb()` instead.
  """
  warnings.warn(
    "Cor_96_wellplate_360ul_Fb() is deprecated and will be removed after 2026-10. "
    "Use cor_96_wellplate_360uL_Fb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_96_wellplate_360uL_Fb(name, with_lid)


def Cor_96_wellplate_360ul_Fb_Lid(name: str) -> Lid:  # remove 2026-10
  """Deprecated alias for cor_96_wellplate_360uL_Fb_lid().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `cor_96_wellplate_360uL_Fb_lid()` instead.
  """
  warnings.warn(
    "Cor_96_wellplate_360ul_Fb_Lid() is deprecated and will be removed after 2026-10. "
    "Use cor_96_wellplate_360uL_Fb_lid() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_96_wellplate_360uL_Fb_lid(name)
