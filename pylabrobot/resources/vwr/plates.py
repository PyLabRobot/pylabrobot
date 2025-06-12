from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def VWR_96_ReagentReservoir_195mL_Ub(name: str) -> Plate:
  """
  VWR NA Cat. No. 77575-302

  For plate carriers and plate stacks
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=31.4,
    lid=None,
    model="VWR_96_ReagentReservoir_195mL_Ub",
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=8.9,
      size_y=8.9,
      size_z=26.85,
      dx=9.93,  # 14.38 - (8.9/2)
      dy=6.79,  # 11.24 - (8.9/2)
      dz=3.55,
      material_z_thickness=1,  # 31.4 - (26.85 + 3.55)
      item_dx=9.0,
      item_dy=9.0,
      num_items_x=12,
      num_items_y=8,
      cross_section_type=CrossSectionType.RECTANGLE,
      bottom_type=WellBottomType.U,
      max_volume=195000,
    ),
  )
