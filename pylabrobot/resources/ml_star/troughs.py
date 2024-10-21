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
  a, b, c, d = -9.31148824e-2, 17.4143864, 2639.07733, -6103.77862
  polynomial_fit_of_empirical_data = a * h**3 + b * h**2 + c * h + d
  return round(polynomial_fit_of_empirical_data, 3)

def _compute_height_from_volume_Hamilton_1_trough_200ml_Vb(liquid_volume: float):
  """ Function to compute height of liquid in trough,
  based on poylonmial fit of z-probed, empirical data.
  """
  a, b, c, d = 3.59536348e-16, -2.59979679e-10, 0.000331809032, 2.70090777
  polynomial_fit_of_empirical_data = a * liquid_volume**3 + b * liquid_volume**2 + c * liquid_volume + d
  return round(polynomial_fit_of_empirical_data, 3)

# Calculation accuracy data:
# input_volumes = [0, 6000, 10000, 20000, 50000, 100000, 150000,
# 200000, 240000, 300000]
# target_heights = [0, 5.8, 7.4, 10.1, 18.5, 32.9, 47.8,
# 61.7, 72.6, 88.4]
# calculated_heights = [2.7, 4.68, 5.99, 9.24, 18.69, 33.64, 47.84,
# 61.54, 72.33, 88.55]

def Hamilton_1_trough_200ml_Vb(name: str) -> Trough:
  """ Hamilton cat. no.: 56695-02
  Trough 200ml, w lid, self standing, Black.
  Internal dimensions:
    - size_x=34.0,
    - size_y=115.0,
    - size_z=92.0,
  Compatible with Trough_CAR_4R200_A00 (185436).
  """
  return Trough(
    name=name,
    size_x=37.0,
    size_y=118.0,
    size_z=95.0,
    material_z_thickness=1.5,
    through_base_to_container_base = 1.2,
    max_volume=200_000, # units: ul
    model="Hamilton_1_trough_200ml_Vb",
    bottom_type=TroughBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_Hamilton_1_trough_200ml_Vb,
    compute_height_from_volume=_compute_height_from_volume_Hamilton_1_trough_200ml_Vb,
  )

