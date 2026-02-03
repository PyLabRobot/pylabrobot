"""Definitions for Hamilton-manufactured Troughs"""

import warnings

from pylabrobot.resources.trough import Trough, TroughBottomType
from pylabrobot.utils.interpolation import interpolate_1d

# --------------------------------------------------------------------------- #
# Hamilton 1-trough 60 mL (V-bottom)
# --------------------------------------------------------------------------- #

_hamilton_1_trough_60ml_Vb_height_to_volume_measurements = {
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
_hamilton_1_trough_60ml_Vb_volume_to_height_measurements = {
  v: k for k, v in _hamilton_1_trough_60ml_Vb_height_to_volume_measurements.items()
}


def _compute_volume_from_height_hamilton_1_trough_60ml_Vb(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Hamilton 1-trough 60 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if h < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h > 65.5 * 1.05:
    raise ValueError(f"Height {h} is too large for Hamilton_1_trough_60ml_Vb.")

  vol_ul = interpolate_1d(
    h, _hamilton_1_trough_60ml_Vb_height_to_volume_measurements, bounds_handling="error"
  )
  return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_hamilton_1_trough_60ml_Vb(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Hamilton 1-trough 60 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")

  h_mm = interpolate_1d(
    volume_ul, _hamilton_1_trough_60ml_Vb_volume_to_height_measurements, bounds_handling="error"
  )
  return round(max(0.0, h_mm), 3)


def hamilton_1_trough_60ml_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 56694-01 (white/translucent), 56694-02 (black/conductive)
  Trough 60 mL, w lid, self standing (V-bottom).
  True maximal volume capacity ~80 mL.
  Compatible with Trough_CAR_?? (194057 <- not yet integrated into PLR!).
  """
  warnings.warn(
    "hamilton_1_trough_60ml_Vb has a center support that can interfere with pipetting.\
     If using an odd number of channels, use spread='custom' and define offsets for each channel to avoid collision."
  )

  return Trough(
    name=name,
    size_x=19.0,
    size_y=90.0,
    size_z=65.5,
    material_z_thickness=1.58,
    through_base_to_container_base=1.0,
    max_volume=60_000,  # units: µL
    model=hamilton_1_trough_60ml_Vb.__name__,
    bottom_type=TroughBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_hamilton_1_trough_60ml_Vb,
    compute_height_from_volume=_compute_height_from_volume_hamilton_1_trough_60ml_Vb,
  )


# --------------------------------------------------------------------------- #
# Hamilton 1-trough 120 mL (V-bottom)
# --------------------------------------------------------------------------- #

_hamilton_1_trough_120mL_Vb_height_to_volume_measurements = {
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
_hamilton_1_trough_120mL_Vb_volume_to_height_measurements = {
  v: k for k, v in _hamilton_1_trough_120mL_Vb_height_to_volume_measurements.items()
}


def _compute_volume_from_height_hamilton_1_trough_120mL_Vb(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Hamilton 1-trough 120 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if h < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h > 80.0 * 1.05:
    raise ValueError(f"Height {h} is too large for hamilton_1_trough_120ml_Vb.")

  vol_ul = interpolate_1d(
    h, _hamilton_1_trough_120mL_Vb_height_to_volume_measurements, bounds_handling="error"
  )
  return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_hamilton_1_trough_120mL_Vb(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Hamilton 1-trough 120 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")

  h_mm = interpolate_1d(
    volume_ul, _hamilton_1_trough_120mL_Vb_volume_to_height_measurements, bounds_handling="error"
  )
  return round(max(0.0, h_mm), 3)


def hamilton_1_trough_120mL_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 194052 (white/translucent)
  Trough 120 mL, without lid, self standing (V-bottom).
  True maximal volume capacity ~120 mL.
  Compatible with Trough_CAR_?? (194058 <- not yet integrated into PLR!).
  """
  warnings.warn(
    "hamilton_1_trough_120ml_Vb has 3 (!) in-container support beams that can interfere with "
    "pipetting. If using an odd number of channels, use spread='custom' and define offsets "
    "for each channel to avoid collision."
  )

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
    compute_volume_from_height=_compute_volume_from_height_hamilton_1_trough_120mL_Vb,
    compute_height_from_volume=_compute_height_from_volume_hamilton_1_trough_120mL_Vb,
  )


# --------------------------------------------------------------------------- #
# Hamilton 1-trough 200 mL (V-bottom)
# --------------------------------------------------------------------------- #

_hamilton_1_trough_200ml_Vb_height_to_volume_measurements = {
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
_hamilton_1_trough_200ml_Vb_volume_to_height_measurements = {
  v: k for k, v in _hamilton_1_trough_200ml_Vb_height_to_volume_measurements.items()
}


def _compute_volume_from_height_hamilton_1_trough_200ml_Vb(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Hamilton 1-trough 200 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if h < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h > 95 * 1.05:
    raise ValueError(f"Height {h} is too large for Hamilton_1_trough_200ml_Vb.")

  vol_ul = interpolate_1d(
    h, _hamilton_1_trough_200ml_Vb_height_to_volume_measurements, bounds_handling="error"
  )
  return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_hamilton_1_trough_200ml_Vb(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Hamilton 1-trough 200 mL (V-bottom, conductive),
  using piecewise linear interpolation.
  """
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")

  h_mm = interpolate_1d(
    volume_ul, _hamilton_1_trough_200ml_Vb_volume_to_height_measurements, bounds_handling="error"
  )
  return round(max(0.0, h_mm), 3)


def hamilton_1_trough_200ml_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 56695-01 (white/translucent), 56695-02 (black/conductive)
  Trough 200 mL, w lid, self standing (V-bottom).
  True maximal volume capacity ~300 mL.
  Compatible with Trough_CAR_4R200_A00 (185436).
  """
  return Trough(
    name=name,
    size_x=37.0,
    size_y=118.0,
    size_z=95.0,
    material_z_thickness=1.5,
    through_base_to_container_base=1.2,
    max_volume=200_000,  # units: µL
    model=hamilton_1_trough_200ml_Vb.__name__,
    bottom_type=TroughBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_hamilton_1_trough_200ml_Vb,
    compute_height_from_volume=_compute_height_from_volume_hamilton_1_trough_200ml_Vb,
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def Hamilton_1_trough_200ml_Vb(name: str) -> Trough:  # remove 2026-01
  """Deprecated alias for hamilton_1_trough_200ml_Vb().

  This alias will be removed after 2026-01. Use the lowercase
  `hamilton_1_trough_200ml_Vb()` instead.
  """
  warnings.warn(
    "Hamilton_1_trough_200ml_Vb() is deprecated and will be removed after 2026-01. "
    "Use hamilton_1_trough_200ml_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_1_trough_200ml_Vb(name)
