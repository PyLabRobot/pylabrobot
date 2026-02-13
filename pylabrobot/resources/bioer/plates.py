# BioER 2.2 mL deepwell with polynomial height<->volume mapping
# Uses fit: h(V) = a3*V^3 + a2*V^2 + a1*V + a0   (V in µL, h in mm)

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# Measured the height of vols in the plate. Graphed and then fitted polynomial.
# Polynomial more accurate than circular conical frustum.

# ---- Polynomial coefficients from your fit (units: µL -> mm) ----
_A3 = 2.34770904e-09
_A2 = -9.12279010e-06
_A1 = 2.68872240e-02
_A0 = 2.06530412e00

# ---- Geometry / limits (from your earlier spec & measurements) ----
_WELL_TOP_SIDE_MM = 8.25  # inner opening (square), mm
_WELL_DEPTH_MM = 42.4  # well depth, mm
_MAX_VOL_UL = 2200.0  # vendor spec, µL


# Monotone cubic on [0, MAX_VOL] from your data — use binary search for inversion
def _height_from_volume_poly(vol_ul: float) -> float:
  """Height (mm) from volume (µL) using the fitted cubic."""
  v = max(0.0, min(float(vol_ul), _MAX_VOL_UL))
  h = ((_A3 * v + _A2) * v + _A1) * v + _A0
  # Clamp to physical depth
  if h < 0.0:
    return 0.0
  if h > _WELL_DEPTH_MM:
    return _WELL_DEPTH_MM
  return h


def _volume_from_height_poly(h_mm: float, *, tol: float = 1e-6, max_iter: int = 64) -> float:
  """Volume (µL) from height (mm) by inverting the cubic with binary search."""
  h_target = max(0.0, min(float(h_mm), _WELL_DEPTH_MM))
  lo, hi = 0.0, _MAX_VOL_UL
  # Quick outs
  if h_target <= _height_from_volume_poly(lo):
    return 0.0
  if h_target >= _height_from_volume_poly(hi):
    return _MAX_VOL_UL
  for _ in range(max_iter):
    mid = 0.5 * (lo + hi)
    h_mid = _height_from_volume_poly(mid)
    if abs(h_mid - h_target) <= tol:
      return mid
    if h_mid < h_target:
      lo = mid
    else:
      hi = mid
  return 0.5 * (lo + hi)


def BioER_96_wellplate_Vb_2200uL(name: str) -> Plate:
  """BioER Cat. No. BSH06M1T-A (KingFisher-compatible)
  Spec: https://en.bioer.com/uploadfiles/2024/05/20240513165756879.pdf
  """
  well_kwargs = {
    "size_x": _WELL_TOP_SIDE_MM,
    "size_y": _WELL_TOP_SIDE_MM,
    "size_z": _WELL_DEPTH_MM,
    "bottom_type": WellBottomType.V,  # physical bottom shape
    "cross_section_type": CrossSectionType.RECTANGLE,
    "material_z_thickness": 0.8,  # measured
    "max_volume": _MAX_VOL_UL,
    # ---- height<->volume mapping used by PLR ----
    "compute_height_from_volume": lambda vol_ul: _height_from_volume_poly(vol_ul),
    "compute_volume_from_height": lambda h_mm: _volume_from_height_poly(h_mm),
  }

  return Plate(
    name=name,
    size_x=127.1,  # spec
    size_y=85.0,  # spec
    size_z=44.2,  # spec
    lid=None,
    model=BioER_96_wellplate_Vb_2200uL.__name__,
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
