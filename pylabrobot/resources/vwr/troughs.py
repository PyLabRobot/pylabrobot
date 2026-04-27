from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_rectangle,
  compute_volume_from_height_rectangle,
)
from pylabrobot.resources.trough import Trough, TroughBottomType


def VWR_1_trough_195000uL_Ub(name: str) -> Trough:
  """VWR NA Cat. No. 77575-302"""

  inner_width = 127.76 - (14.38 - 8.9 / 2) * 2
  inner_length = 85.48 - (11.24 - 8.9 / 2) * 2

  return Trough(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=31.4,  # from spec
    material_z_thickness=3.55,  # from spec
    max_volume=195000,  # from spec 195 mL
    model=VWR_1_trough_195000uL_Ub.__name__,
    bottom_type=TroughBottomType.U,
    compute_height_from_volume=lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume,
      inner_length,
      inner_width,
    ),
    compute_volume_from_height=lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height,
      inner_length,
      inner_width,
    ),
  )


def VWRReagentReservoirs25mL(name: str) -> Trough:
  """part number 89094"""
  return Trough(
    name=name,
    size_x=44,
    size_y=127,
    size_z=25,
    max_volume=25000,
    model="VWR Reagent Reservoirs 25mL",
  )
