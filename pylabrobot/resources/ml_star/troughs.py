""" Definitions for Hamilton-manufactured Troughs """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from pylabrobot.resources.trough import (
  Trough,
  TroughBottomType
)

# # # # # # # # # # Hamilton_1_trough_200ml_Vb # # # # # # # # # #


def _compute_volume_from_height_Hamilton_1_trough_200ml_Vb(h: float):
    """ Function to compute volume of liquid in trough,
    based on poylonmial fit of z-probed, empirical data.
    """
    if h > 89: # Maximal measured height possible
      raise ValueError(f"Height {h} is too large for Hamilton_1_trough_200ml_Vb")
    a, b, c, d = 5.50216554e-02, -5.30245236, 3.61064575e+03, -1.56155485e+04
    polynomial_fit_of_empirical_data = a * h**3 + b * h**2 + c * h + d
    return polynomial_fit_of_empirical_data

def _compute_height_from_volume_Hamilton_1_trough_200ml_Vb(liquid_volume: float):
    """ Function to compute height of liquid in trough,
    based on poylonmial fit of z-probed, empirical data.
    """
    a, b, c, d = -3.22112575e-16, 9.25015048e-11, 0.000281288611, 4.34137097
    polynomial_fit_of_empirical_data = a * liquid_volume**3 + b * liquid_volume**2 + c * liquid_volume + d
    return round(polynomial_fit_of_empirical_data, 3)

# Calculation accuracy data:
# input_volumes = [6_000,  10_000, 20_000, 50_000, 100_000, 150_000, 200_000,
# 240_000, 300_000] in ul
# target_heights = [5.8, 7.4, 10.1, 18.5, 32.9, 47.8, 61.7,
# 72.6, 88.4] in mm
# calculated_heights = [6.032, 7.163, 10.002, 18.597, 33.073, 47.529, 61.722,
# 72.726, 88.356] in mm

def Hamilton_1_trough_200ml_Vb(name: str) -> Trough:
  """ Hamilton cat. no.: 56695-02
  Trough 200ml, w lid, self standing, Black.
  Internal dimensions:
    - size_x=34.0,
    - size_y=115.0,
    - size_z=92.0,
  """
  return Trough(
    name=name,
    size_x=37.0,
    size_y=118.0,
    size_z=95.0,
    material_z_thickness=1.5,
    true_dz = 1.2,
    max_volume=200_000, # units: ul
    model="Hamilton_1_trough_200ml_Vb",
    bottom_type=TroughBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_Hamilton_1_trough_200ml_Vb,
    compute_height_from_volume=_compute_height_from_volume_Hamilton_1_trough_200ml_Vb,
  )

