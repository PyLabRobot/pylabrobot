"""Definitions for Hamilton-manufactured Troughs"""

from pylabrobot.resources.trough import Trough, TroughBottomType


# # # # # # # # # # hamilton_1_trough_60ml_Vb # # # # # # # # # #

def _compute_volume_from_height_hamilton_1_trough_60ml_Vb(h: float) -> float:
    """Compute liquid volume (µL) in the 1-trough 60 mL (Vb) from observed height (mm),
    using a cubic fit of empirical data (height → volume).
    """
    if h < 0:
        raise ValueError("Height must be ≥ 0 mm")
    if h > 65.5 * 1.05: # height of container * 5% inaccuracy tolerance
        raise ValueError(f"Height {h} is too large for Hamilton_1_trough_60ml_Vb")

    # Fit: volume = a*h^3 + b*h^2 + c*h + d
    a, b, c, d = (
        -1.22114990e-01,
         1.46760598e+01,
         9.54700331e+02,
        -2.05206563e+03,
    )
    vol_ul = a * h**3 + b * h**2 + c * h + d
    # avoid tiny negatives from numerical noise
    return round(max(0.0, vol_ul), 3)


def _compute_height_from_volume_hamilton_1_trough_60ml_Vb(liquid_volume: float) -> float:
    """Compute observed height (mm) from liquid volume (µL) in the 1-trough 60 mL (Vb),
    using a cubic fit of empirical data (volume → height).
    """
    if liquid_volume < 0:
        raise ValueError(f"Volume must be ≥ 0 µL; got {liquid_volume} µL")

    # Fit: height = a*V^3 + b*V^2 + c*V + d
    a, b, c, d = (
        2.30274090e-14,
       -3.90039954e-09,
        8.68088064e-04,
        2.41366625e+00,
    )
    h_mm = a * liquid_volume**3 + b * liquid_volume**2 + c * liquid_volume + d
    return round(max(0.0, h_mm), 3)

# Empirical data used for fitting:
# results_measurement_fitting_dict = {
#     "Volume (µL)": [
#         0, 500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500, 4_000, 4_500, 5_000,
#         5_500, 6_000, 7_000, 8_000, 9_000, 10_000, 20_000, 30_000, 45_000,
#         60_000, 70_000, 80_000,
#     ],
#     "Observed Height (mm)": [
#         0.0, 2.2, 3.5, 4.0, 4.7, 5.2, 5.6, 6.0, 6.3, 6.7, 6.8, 7.2, 7.5,
#         8.3, 9.0, 9.8, 10.4, 18.0, 25.3, 35.6, 45.7, 52.13, 58.5,
#     ],
# }

def hamilton_1_trough_60ml_Vb(name: str) -> Trough:
    """Hamilton cat. no.: ? (black/conductive)
    Trough 60ml, w lid, self standing.
    True maximal volume capacity ~80 mL.
    Compatible with Trough_CAR_?? (??).
    """
    return Trough(
        name=name,
        size_x=19.0,
        size_y=90.0,
        size_z=65.5,
        material_z_thickness=1.58,
        through_base_to_container_base=1.0,
        max_volume=60_000,  # units: ul
        model=hamilton_1_trough_60ml_Vb.__name__,
        bottom_type=TroughBottomType.V,
        compute_volume_from_height=_compute_volume_from_height_hamilton_1_trough_60ml_Vb,
        compute_height_from_volume=_compute_height_from_volume_hamilton_1_trough_60ml_Vb,
    )


# # # # # # # # # # hamilton_1_trough_200ml_Vb # # # # # # # # # #


def _compute_volume_from_height_hamilton_1_trough_200ml_Vb(h: float):
  """Function to compute volume of liquid in trough,
  based on poylonmial fit of z-probed, empirical data.
  """
  if h > 89:  # Maximal measured height possible
    raise ValueError(f"Height {h} is too large for Hamilton_1_trough_200ml_Vb")
  a, b, c, d = -9.31148824e-2, 17.4143864, 2639.07733, -6103.77862
  polynomial_fit_of_empirical_data = a * h**3 + b * h**2 + c * h + d
  return round(polynomial_fit_of_empirical_data, 3)


def _compute_height_from_volume_hamilton_1_trough_200ml_Vb(
  liquid_volume: float,
):
  """Function to compute height of liquid in trough,
  based on poylonmial fit of z-probed, empirical data.
  """
  a, b, c, d = (
    3.59536348e-16,
    -2.59979679e-10,
    0.000331809032,
    2.70090777,
  )
  polynomial_fit_of_empirical_data = (
    a * liquid_volume**3 + b * liquid_volume**2 + c * liquid_volume + d
  )
  return round(polynomial_fit_of_empirical_data, 3)


# Calculation accuracy data:
# input_volumes = [0, 6000, 10000, 20000, 50000, 100000, 150000,
# 200000, 240000, 300000]
# target_heights = [0, 5.8, 7.4, 10.1, 18.5, 32.9, 47.8,
# 61.7, 72.6, 88.4]
# calculated_heights = [2.7, 4.68, 5.99, 9.24, 18.69, 33.64, 47.84,
# 61.54, 72.33, 88.55]


def hamilton_1_trough_200ml_Vb(name: str) -> Trough:
  """Hamilton cat. no.: 56695-02 (black/conductive)
  Trough 200ml, w lid, self standing.
  True maximal volume capacity ~300 mL.
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
    through_base_to_container_base=1.2,
    max_volume=200_000,  # units: ul
    model=hamilton_1_trough_200ml_Vb.__name__,
    bottom_type=TroughBottomType.V,
    compute_volume_from_height=_compute_volume_from_height_hamilton_1_trough_200ml_Vb,
    compute_height_from_volume=_compute_height_from_volume_hamilton_1_trough_200ml_Vb,
  )

# Deprecated function names kept for backward compatibility
def Hamilton_1_trough_200ml_Vb(name: str) -> Trough: # remove 2026-01
  return hamilton_1_trough_200ml_Vb(name)
