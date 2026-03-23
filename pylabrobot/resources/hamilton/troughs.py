"""Definitions for Hamilton-manufactured Troughs"""

import warnings

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.trough import Trough, TroughBottomType

# --------------------------------------------------------------------------- #
# Hamilton 1-trough 60 mL (V-bottom)
# --------------------------------------------------------------------------- #

# Calibration data: height (mm) → volume (µL).
# Obtained via ztouch probing of cavity_bottom, manual addition of known volumes,
# and LLD measurement of liquid height relative to cavity_bottom.
_hamilton_1_trough_60mL_Vb_height_volume_data = {
  0.0: 0.0,
  2.2: 500.0,
  3.5: 1_000.0,
  4.0: 1_500.0,
  4.7: 2_000.0,
  5.2: 2_500.0,
  5.6: 3_000.0,
  6.0: 3_500.0,
  6.3: 4_000.0,
  6.7: 4_500.0,
  6.8: 5_000.0,
  7.2: 5_500.0,
  7.5: 6_000.0,
  8.3: 7_000.0,
  9.0: 8_000.0,
  9.8: 9_000.0,
  10.4: 10_000.0,
  18.0: 20_000.0,
  25.3: 30_000.0,
  35.6: 45_000.0,
  45.7: 60_000.0,
  52.13: 70_000.0,
  58.5: 80_000.0,
}


def hamilton_1_trough_60mL_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 56694-01 (white/translucent), 56694-02 (black/conductive)
  Trough 60 mL, w lid, self standing (V-bottom).
  True maximal volume capacity ~80 mL.
  Compatible with Trough_CAR_?? (194057 <- not yet integrated into PLR!).
  Has a center support wall (~1.2mm wide at Y=44-46mm) but is still open
  at the bottom.
  """

  return Trough(
    name=name,
    size_x=19.0,
    size_y=90.0,
    size_z=65.5,
    material_z_thickness=1.58,
    through_base_to_container_base=1.0,
    max_volume=60_000,  # units: µL
    model=hamilton_1_trough_60mL_Vb.__name__,
    bottom_type=TroughBottomType.V,
    height_volume_data=_hamilton_1_trough_60mL_Vb_height_volume_data,
    no_go_zones=[
      (Coordinate(0, 44.4, 5.0), Coordinate(19.0, 45.6, 60.25)),  # center divider
    ],
  )


# --------------------------------------------------------------------------- #
# Hamilton 1-trough 120 mL (V-bottom)
# --------------------------------------------------------------------------- #

_hamilton_1_trough_120mL_Vb_height_volume_data = {
  0.0: 0.0,
  5.85: 4_000.0,
  6.3: 6_000.0,
  6.98: 8_000.0,
  7.72: 10_000.0,
  8.48: 12_000.0,
  9.82: 15_000.0,
  13.05: 20_000.0,
  18.25: 30_000.0,
  23.29: 40_000.0,
  33.07: 60_000.0,
  42.42: 80_000.0,
  51.55: 100_000.0,
  61.87: 120_000.0,
  70.62: 140_000.0,
  80.0: 160_000.0,
}


def hamilton_1_trough_120mL_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 194052 (white/translucent)
  Trough 120 mL, without lid, self standing (V-bottom).
  True maximal volume capacity ~120 mL.
  Compatible with Trough_CAR_?? (194058 <- not yet integrated into PLR!).
  Has 3 in-container support beams (~2.5mm wide at base, ~0.8mm at top, tapered)
  but is still open at the bottom.
  """

  return Trough(
    name=name,
    size_x=19.0,
    size_y=142.5,
    size_z=80.0,
    material_z_thickness=1.54,  # ztouch measured
    through_base_to_container_base=1.1,  # ztouch measured
    max_volume=120_000,  # units: µL
    model=hamilton_1_trough_120mL_Vb.__name__,
    bottom_type=TroughBottomType.V,
    height_volume_data=_hamilton_1_trough_120mL_Vb_height_volume_data,
    no_go_zones=[
      (Coordinate(0, 39.7, 12.0), Coordinate(19.0, 42.2, 70.0)),  # beam 1
      (Coordinate(0, 73.5, 12.0), Coordinate(19.0, 76.0, 70.0)),  # beam 2
      (Coordinate(0, 107.3, 12.0), Coordinate(19.0, 109.8, 70.0)),  # beam 3
    ],
  )


# --------------------------------------------------------------------------- #
# Hamilton 1-trough 200 mL (V-bottom)
# --------------------------------------------------------------------------- #

_hamilton_1_trough_200mL_Vb_height_volume_data = {
  0.0: 0.0,
  5.8: 6_000.0,
  7.4: 10_000.0,
  10.1: 20_000.0,
  18.5: 50_000.0,
  32.9: 100_000.0,
  47.8: 150_000.0,
  61.7: 200_000.0,
  72.6: 240_000.0,
  88.4: 300_000.0,
}


def hamilton_1_trough_200mL_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 56695-01 (white/translucent), 56695-02 (black/conductive)
  Trough 200 mL, w lid, self standing (V-bottom).
  True maximal volume capacity ~300 mL.
  Compatible with Trough_CAR_4R200_A00 (185436).
  Has a center support wall (~1.2mm wide at Y=59-61mm) which is open at the bottom.
  """
  return Trough(
    name=name,
    size_x=37.0,
    size_y=118.0,
    size_z=95.0,
    material_z_thickness=1.5,
    through_base_to_container_base=1.2,
    max_volume=200_000,  # units: µL
    model=hamilton_1_trough_200mL_Vb.__name__,
    bottom_type=TroughBottomType.V,
    height_volume_data=_hamilton_1_trough_200mL_Vb_height_volume_data,
    no_go_zones=[
      (Coordinate(0, 60, 8.0), Coordinate(19.0, 61.7, 60.0))  # center divider
    ],
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Hamilton_1_trough_200ml_Vb(name: str) -> Trough:  # remove 2026-07
  """Deprecated alias for hamilton_1_trough_200mL_Vb().

  This alias will be removed after 2026-07 in the dev branch and PLR v1 (whichever you are using).
  Use `hamilton_1_trough_200mL_Vb()` instead.
  """
  warnings.warn(
    "Hamilton_1_trough_200ml_Vb() is deprecated and will be removed after 2026-07. "
    "Use hamilton_1_trough_200mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_1_trough_200mL_Vb(name)


def hamilton_1_trough_200ml_Vb(name: str) -> Trough:  # remove 2026-07
  """Deprecated alias for hamilton_1_trough_200mL_Vb().

  This alias will be removed after 2026-07 in the dev branch and PLR v1 (whichever you are using).
  Use `hamilton_1_trough_200mL_Vb()` instead (note capital L in 'mL').
  """
  warnings.warn(
    "hamilton_1_trough_200ml_Vb() is deprecated and will be removed after 2026-07. "
    "Use hamilton_1_trough_200mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_1_trough_200mL_Vb(name)


def hamilton_1_trough_60ml_Vb(name: str) -> Trough:  # remove 2026-07
  """Deprecated alias for hamilton_1_trough_60mL_Vb().

  This alias will be removed after 2026-07 in the dev branch and PLR v1 (whichever you are using).
  Use `hamilton_1_trough_60mL_Vb()` instead (note capital L in 'mL').
  """
  warnings.warn(
    "hamilton_1_trough_60ml_Vb() is deprecated and will be removed after 2026-07. "
    "Use hamilton_1_trough_60mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_1_trough_60mL_Vb(name)
