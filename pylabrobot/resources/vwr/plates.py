from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def VWR_1_troughplate_195000uL_Ub(name: str) -> Plate:
  """VWR NA Cat. No. 77575-302"""
  # technical drawing:
  # https://github.com/PyLabRobot/pylabrobot/pull/574#issuecomment-2967988150

  # product info
  # https://us-prod2.vwr.com/store/product/47763965/vwr-multi-channel-polypropylene-reagent-reservoirs

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=31.4,  # from spec
    lid=None,
    model=VWR_1_troughplate_195000uL_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=127.76 - (14.38 - 8.9 / 2) * 2,  # from spec
      size_y=85.48 - (11.24 - 8.9 / 2) * 2,  # from spec
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
      max_volume=195000,  # from spec 195 mL
    ),
  )


def VWR_96_DWP_2mL_Vb(name: str) -> Plate:
  """VWR NA Cat. No. 76329-998"""
  # product info
  # https://us-prod2.vwr.com/store/product/26915641/vwr-96-well-deep-well-plates-with-automation-notches

  # no technical drawing available

  return Plate(
    name=name,
    size_x=127.76,  # standard
    size_y=85.48,  # standard
    size_z=44.0,  # measured
    lid=None,
    model="VWR_96_DWP_2mL_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=10.0,  # measured
      size_y=10.0,  # measured
      size_z=43.5,  # measured
      dx=9.0,  # measured
      dy=5.5,  # measured
      dz=0.5,  # 44.0 - 43.5 = 0.5
      material_z_thickness=1,  # estimate
      item_dx=10.0,  # measured
      item_dy=10.0,  # measured
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,
      bottom_type=WellBottomType.V,
      max_volume=2200,  # from spec 2.2 ml
    ),
  )
