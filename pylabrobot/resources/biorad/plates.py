import warnings

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def biorad_384_wellplate_50uL_Vb(name: str) -> Plate:
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=10.40,
    lid=None,
    model="biorad_384_wellplate_50uL_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.58,
      dy=7.44,
      dz=1.05,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.10,
      size_y=3.10,
      size_z=9.35,
      bottom_type=WellBottomType.V,
      material_z_thickness=1,  # measured
      cross_section_type=CrossSectionType.CIRCLE,
      name_prefix=name,
    ),
  )


# Calibration data: height (mm) → volume (µL), measured per well.
_biorad_96_wellplate_200uL_Vb_height_volume_data = {
  0.0: 0.0,
  1.17: 5.0,
  1.91: 10.0,
  3.13: 20.0,
  5.11: 40.0,
  6.57: 60.0,
  7.84: 80.0,
  8.83: 100.0,
  10.1: 120.0,
  10.9: 140.0,
  12.08: 160.0,
  12.84: 180.0,
  13.71: 200.0,
  14.27: 220.0,
  15.19: 240.0,
}


def biorad_96_wellplate_200uL_Vb(name: str) -> Plate:
  """Bio-Rad Hard-Shell 96-well PCR plate, low profile, thin wall, skirted.

  - Part no.: HSP9601 (white shell, clear wells).
  - Total volume/well: 200 µL.
  """
  well_diameter = 5.46
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=16.06,
    lid=None,
    model=biorad_96_wellplate_200uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=round(14.38 - well_diameter / 2, 2),
      dy=round(11.24 - well_diameter / 2, 2),
      dz=1.25,
      item_dx=9.0,
      item_dy=9.0,
      size_x=well_diameter,
      size_y=well_diameter,
      size_z=14.4,
      bottom_type=WellBottomType.V,
      material_z_thickness=0.8,
      cross_section_type=CrossSectionType.CIRCLE,
      height_volume_data=_biorad_96_wellplate_200uL_Vb_height_volume_data,
      name_prefix=name,
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def BioRad_384_wellplate_50uL_Vb(name: str) -> Plate:  # remove v1b1
  """Deprecated alias for biorad_384_wellplate_50uL_Vb().

  This alias will be removed in v1b1.
  Use `biorad_384_wellplate_50uL_Vb()` instead.
  """
  warnings.warn(
    "BioRad_384_wellplate_50uL_Vb() is deprecated and will be removed in v1b1. "
    "Use biorad_384_wellplate_50uL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return biorad_384_wellplate_50uL_Vb(name)
