import warnings

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_volume_container_2segments_square_vbottom,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # cor_axy_24_wellplate_10mL_Vb # # # # # # # # # #


def cor_axy_24_wellplate_10mL_Vb(name: str) -> Plate:
  """
  Corning cat. no.: P-DW-10ML-24-C-S
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Genomics-&-Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Axygen%C2%AE-Deep-Well-and-Assay-Plates/p/P-DW-10ML-24-C
  - brand: Axygen
  - distributor: (Fisher Scientific, 12557837)
  - material: Polypropylene
  - sterile: yes
  - autoclavable: yes
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=44.24,
    lid=None,
    model=cor_axy_24_wellplate_10mL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      dx=9.8,
      dy=7.2,
      dz=1.2,
      item_dx=18,
      item_dy=18,
      size_x=17.0,
      size_y=17.0,
      size_z=42,
      material_z_thickness=1.46,
      bottom_type=WellBottomType.V,
      compute_volume_from_height=_compute_volume_from_height_Cor_Axy_24_wellplate_10mL_Vb,
      cross_section_type=CrossSectionType.RECTANGLE,
    ),
  )


def _compute_volume_from_height_Cor_Axy_24_wellplate_10mL_Vb(h: float):
  if h > 42.1:
    raise ValueError(f"Height {h} is too large for Cos_96_Vb")
  return calculate_liquid_volume_container_2segments_square_vbottom(
    x=17, y=17, h_pyramid=5, h_cube=37, liquid_height=h
  )


# # # # # # # # # # cor_axy_96_wellplate_500uL_Ub # # # # # # # # # #


def cor_axy_96_wellplate_500uL_Ub(name: str) -> Plate:
  """
  Axygen 96w Shallow Well Plate 500uL U Bottom
  - Product number: P-96-450V-C-S
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2c/US/en/Genomics-&-Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Axygen%C2%AE-Deep-Well-and-Assay-Plates/p/P-96-450V-C-S
  - Spec sheet info: https://www.corning.com/catalog/cls/documents/selection-guides/CLS-A-PSG-001.pdf
  - working volume: 450uL
  - brand: Axygen
  - material: Polypropylene
  - sterile: yes
  """
  return Plate(
    name=name,
    size_x=127.0,  # measured
    size_y=85.51,  # measured
    size_z=14.82,  # measured
    model=cor_axy_96_wellplate_500uL_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      dx=9.6,  # measured
      dy=7.0,  # measured
      dz=1.2,  # measured
      item_dx=9.0,  # measured
      item_dy=9.0,  # measured
      size_x=8.0,  # measured
      size_y=8.0,  # measured
      size_z=14.82 - 2.57,  # measured
      bottom_type=WellBottomType.U,
      material_z_thickness=1.18,  # measured
      cross_section_type=CrossSectionType.CIRCLE,
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Cor_Axy_24_wellplate_10mL_Vb(name: str, with_lid: bool = False) -> Plate:  # remove v1b1
  """Deprecated alias for cor_axy_24_wellplate_10mL_Vb().

  This alias will be removed in v1b1.
  Use `cor_axy_24_wellplate_10mL_Vb()` instead.
  """
  warnings.warn(
    "Cor_Axy_24_wellplate_10mL_Vb() is deprecated and will be removed in v1b1. "
    "Use cor_axy_24_wellplate_10mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_axy_24_wellplate_10mL_Vb(name)


def Cor_Axy_96_wellplate_500uL_Ub(name: str, with_lid: bool = False) -> Plate:  # remove v1b1
  """Deprecated alias for cor_axy_96_wellplate_500uL_Ub().

  This alias will be removed in v1b1.
  Use `cor_axy_96_wellplate_500uL_Ub()` instead.
  """
  warnings.warn(
    "Cor_Axy_96_wellplate_500uL_Ub() is deprecated and will be removed in v1b1. "
    "Use cor_axy_96_wellplate_500uL_Ub() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cor_axy_96_wellplate_500uL_Ub(name)
