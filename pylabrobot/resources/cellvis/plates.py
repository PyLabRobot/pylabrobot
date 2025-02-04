from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def CellVis_24_wellplate_3600uL_Fb_Lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.15,  # from spec
    size_y=85.05,  # from spec
    size_z=10,  # from spec
    nesting_z_height=7.5,  # from spec
    model="CellVis_24_wellplate_3600uL_Fb_Lid",
  )


def CellVis_24_wellplate_3600uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """p/n P24-1.5P

  https://www.cellvis.com/_24-well-plate-with--number-1.5-glass-like-polymer-coverslip-bottom-tissue-culture-treated-for-better-cell-attachment-than-cover-glass_/product_detail.php?product_id=65
  """

  return Plate(
    name=name,
    size_x=127.5,  # from spec
    size_y=85.25,  # from spec
    size_z=16.1,  # from spec
    lid=CellVis_24_wellplate_3600uL_Fb_Lid(name + "_Lid") if with_lid else None,
    model="CellVis_24_wellplate_3600uL_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      dx=17.05 - (15.54 / 2),  # from spec
      dy=13.67 - (15.54 / 2),  # from spec
      dz=1 - 0.25,  # from spec
      item_dx=19.3,  # from spec
      item_dy=19.3,  # from spec
      size_x=15.54,  # from spec
      size_y=15.54,  # from spec
      size_z=19,  # from spec
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.25,
      cross_section_type=CrossSectionType.CIRCLE,
    ),
  )
