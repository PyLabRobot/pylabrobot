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
  # technical drawing:
  # https://github.com/PyLabRobot/pylabrobot/pull/574#issuecomment-2967988150
  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=31.4,  # from spec
    lid=None,
    model="VWR_96_ReagentReservoir_195mL_Ub",
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=8.9,  # from spec
      size_y=8.9,  # from spec
      size_z=26.85,  # from spec
      dx=9.93,  # 14.38 - (8.9/2) from spec
      dy=6.79,  # 11.24 - (8.9/2) from spec
      dz=3.55,  # from spec
      material_z_thickness=1,  # 31.4 - (26.85 + 3.55) from spec
      item_dx=9.0,  # from spec
      item_dy=9.0,  # from spec
      num_items_x=1,  # from spec
      num_items_y=1,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,
      bottom_type=WellBottomType.U,
      max_volume=195000,  # from spec
    ),
  )
