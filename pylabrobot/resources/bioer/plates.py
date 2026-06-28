# BioER 2.2 mL deep-well plate (96 wells, square V-bottom).
# Height<->volume is an explicit table sampled from the measured fill-height curve
# (13 points, <=0.05 mm vs the original cubic fit); PLR interpolates piecewise-linearly.

import warnings

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


# height (mm) -> volume (uL)
# TODO: re-verify, moved from polynomial to height-volume dict computationally
_HEIGHT_VOLUME = {
  2.065: 0,
  5.804: 146,
  9.352: 299,
  12.712: 459,
  15.989: 631,
  18.669: 784,
  21.451: 954,
  30.379: 1538,
  33.994: 1764,
  36.004: 1882,
  38.001: 1993,
  40.019: 2099,
  42.061: 2200,
}


def bioer_96_wellplate_2200uL_Vb(name: str) -> Plate:
  """BioER Cat. No. BSH06M1T-A (KingFisher-compatible)
  Spec: https://bioer.com.cn/uploadfiles/2024/05/20240513165756879.pdf
  """
  well_kwargs = {
    "size_x": 8.25,  # inner opening (square), mm
    "size_y": 8.25,  # inner opening (square), mm
    "size_z": 42.4,  # well depth, mm
    "bottom_type": WellBottomType.V,  # physical bottom shape
    "cross_section_type": CrossSectionType.RECTANGLE,
    "material_z_thickness": 0.8,  # measured
    "max_volume": 2200.0,  # vendor spec, uL
    "height_volume_data": _HEIGHT_VOLUME,
  }

  return Plate(
    name=name,
    size_x=127.1,  # spec
    size_y=85.0,  # spec
    size_z=44.2,  # spec
    lid=None,
    model=bioer_96_wellplate_2200uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,  # spec
      num_items_y=8,  # spec
      dx=9.5,  # measured (column pitch)
      dy=7.5,  # measured (row pitch)
      dz=6.0,  # calibrated (mounting offset for your deck)
      item_dx=9.0,  # measured
      item_dy=9.0,  # measured
      **well_kwargs,
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def BioER_96_wellplate_Vb_2200uL(name: str) -> Plate:  # remove 2026-10
  """Deprecated alias for bioer_96_wellplate_2200uL_Vb().

  This alias will be removed after 2026-10 in the dev branch and PLR v1 (whichever you are using).
  Use `bioer_96_wellplate_2200uL_Vb()` instead.
  """
  warnings.warn(
    "BioER_96_wellplate_Vb_2200uL() is deprecated and will be removed after 2026-10. "
    "Use bioer_96_wellplate_2200uL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return bioer_96_wellplate_2200uL_Vb(name)
