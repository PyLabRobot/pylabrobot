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
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def BioRad_384_wellplate_50uL_Vb(name: str) -> Plate:  # remove 2026-10
  """Deprecated alias for biorad_384_wellplate_50uL_Vb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `biorad_384_wellplate_50uL_Vb()` instead.
  """
  warnings.warn(
    "BioRad_384_wellplate_50uL_Vb() is deprecated and will be removed after 2026-10. "
    "Use biorad_384_wellplate_50uL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return biorad_384_wellplate_50uL_Vb(name)
