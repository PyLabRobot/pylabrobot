"""Resource definitions for Eppendorf tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType
from pylabrobot.utils.interpolation import interpolate_1d

import warnings

# --------------------------------------------------------------------------- #
# 1.5 mL Eppendorf Tubes
# --------------------------------------------------------------------------- #


def Eppendorf_DNA_LoBind_1_5ml_Vb(name: str, model="Eppendorf_DNA_LoBind_1_5ml_Vb") -> Tube:
  """1.5 mL round-bottom snap-cap Eppendorf tube.

  .. deprecated:: Will be removed in 2026-04
      Use :func:`eppendorf_tube_1500uL_Vb` instead.

  cat. no.: 022431021 (Eppendorf™ DNA LoBind™ Tubes)

  - bottom_type=TubeBottomType.V
  - snap-cap lid
  """
  warnings.warn(
    "Eppendorf_DNA_LoBind_1_5ml_Vb is deprecated and will be removed in 2026-04. "
    "Use eppendorf_tube_1500uL_Vb instead.",
    DeprecationWarning,
    stacklevel=2
  )
  return eppendorf_tube_1500uL_Vb(name=name)


def Eppendorf_Protein_LoBind_1_5ml_Vb(name: str) -> Tube:
  """1.5 mL round-bottom screw-cap Eppendorf tube.

  .. deprecated:: Will be removed in 2026-04
      Use :func:`eppendorf_tube_1500uL_Vb` instead.

  cat. no.: 022431081 (Eppendorf™ Protein LoBind™ Tubes)

  Same as Eppendorf_DNA_LoBind_1_5ml_Vb
  """
  warnings.warn(
    "Eppendorf_Protein_LoBind_1_5ml_Vb is deprecated and will be removed in 2026-04. "
    "Use eppendorf_tube_1500uL_Vb instead.",
    DeprecationWarning,
    stacklevel=2
  )
  return eppendorf_tube_1500uL_Vb(name=name)


def eppendorf_tube_1500uL_Vb(name: str) -> Tube:
  """Eppendorf cat. no.: 022363204

  1.5 mL or 1_500 uL snap-cap Eppendorf tube with V-bottom;
  nickname: the original "Eppi".

  - Colour: transparent
  - alternative cat. no.:
    - 022431021: DNA LoBind®
    - 022431081: Protein LoBind®
  - Material: Polypropylene
  - Sterilized: ?
  - Autoclavable: Yes ("when open (121 °C, 20 min)")
  - Chemical resistance:?
  - Thermal resistance: ?
  - Sealing options: na
  - Centrifugation safety: Maximum safety and stability for centrifugation up to 25,000 x g
  - Total volume = 5_000 ul
  - URL: https://www.eppendorf.com/us-en/Products/Lab-Consumables/Lab-Tubes/Eppibr-Eppendorf-Safe-Lock-Tubes-p-022363204
  - technical drawing: ./engineering_diagrams/Eppendorf_5mL_snapcap_tube.pdf
  """
  diameter = 9.85  # measured
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=38.9,  # measured
    model=eppendorf_tube_1500uL_Vb.__name__,
    max_volume=1_500,  # units: ul
    material_z_thickness=1.3,
    bottom_type=TubeBottomType.V,
    # compute_volume_from_height=_compute_volume_from_height_eppendorf_tube_1500uL_Vb, TODO
    # compute_height_from_volume=_compute_height_from_volume_eppendorf_tube_1500uL_Vb, TODO
  )


# --------------------------------------------------------------------------- #
# 2 mL Eppendorf Tubes
# --------------------------------------------------------------------------- #


def Eppendorf_DNA_LoBind_2ml_Ub(name: str) -> Tube:
  """2 mL round-bottom snap-cap Eppendorf tube. cat. no.: 022431048

  - bottom_type=TubeBottomType.U
  - snap-cap lid
  """
  diameter = 10.33  # measured
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=41,  # measured
    model="Eppendorf_DNA_LoBind_2ml_Ub",
    max_volume=2000,  # units: ul
    material_z_thickness=0.8,  # measured
  )


# --------------------------------------------------------------------------- #
# 5 mL Eppendorf Tubes
# --------------------------------------------------------------------------- #

_eppendorf_tube_5mL_Vb_snapcap_height_to_volume_measurements = {
  0.0: 0.0,
  3.5: 10.0,
  4.7: 50.0,
  4.7: 100.0,
  4.7: 200.0,
  4.7: 400.0,
  4.7: 600.0,
  4.7: 800.0,
  4.7: 1000.0,
  4.8: 1_200.0,
  5.2: 1_500.0,
  5.6: 2_000.0,
  6.0: 3_000.0,
  6.3: 4_000.0,
  6.7: 4_500.0,
  6.8: 5_000.0,
  7.2: 5_500.0,
}
_eppendorf_tube_5mL_Vb_snapcap_volume_to_height_measurements = {
  v: k for k, v in _eppendorf_tube_5mL_Vb_snapcap_height_to_volume_measurements.items()
}


def _compute_volume_from_height_eppendorf_tube_5mL_Vb_snapcap(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Eppendorf 5 mL V-bottom snap-cap tube,
  using piecewise linear interpolation.
  """
  if h < 0:
    raise ValueError("Height must be ≥ 0 mm.")
  if h > 55.4 * 1.05:
    raise ValueError(f"Height {h} is too large for Eppendorf_tube_5mL_Vb_snapcap.")

  vol_ul = interpolate_1d(
    h, _eppendorf_tube_5mL_Vb_snapcap_height_to_volume_measurements, bounds_handling="error"
  )
  return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_eppendorf_tube_5mL_Vb_snapcap(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Eppendorf 5 mL V-bottom snap-cap tube,
  using piecewise linear interpolation.
  """
  if volume_ul < 0:
    raise ValueError(f"Volume must be ≥ 0 µL; got {volume_ul} µL")

  h_mm = interpolate_1d(
    volume_ul, _eppendorf_tube_5mL_Vb_snapcap_volume_to_height_measurements, bounds_handling="error"
  )
  return round(max(0.0, h_mm), 3)


def eppendorf_tube_5mL_Vb_snapcap(name: str) -> Tube:
  """Eppendorf cat. no.: 0030119401

  5 mL snap-cap Eppendorf tube with V-bottom.

  - Colour: transparent
  - Material: Polypropylene
  - Sterilized: No
  - Autoclavable: No
  - Chemical resistance:?
  - Thermal resistance: ?
  - Surface treatment: available as LoBind™ version
  - Sealing options: na
  - Centrifugation safety: Maximum safety and stability for centrifugation up to 25,000 x g
  - Total volume = 5_000 ul
  - URL: https://www.eppendorf.com/se-en/Products/Lab-Consumables/Lab-Tubes/EppendorfTubes-50mL-p-0030119401
  - technical drawing: ./engineering_diagrams/Eppendorf_5mL_snapcap_tube.pdf
  """
  diameter = 16.7
  return Tube(
    name=name,
    size_x=diameter,
    size_y=diameter,
    size_z=56.7,
    model=eppendorf_tube_5mL_Vb_snapcap.__name__,
    max_volume=5_000,  # units: ul
    material_z_thickness=1.3,
    bottom_type=TubeBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_eppendorf_tube_5mL_Vb_snapcap,
    compute_height_from_volume=_compute_height_from_volume_eppendorf_tube_5mL_Vb_snapcap,
  )
