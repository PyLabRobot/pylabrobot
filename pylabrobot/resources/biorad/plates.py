from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def BioRad_384_wellplate_50uL_Vb(name: str) -> Plate:
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=10.40,
    lid=None,
    model="BioRad_384_wellplate_50uL_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.58,
      dy=7.44,
      dz=1.05,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.10,
      size_y=3.10,
      size_z=9.35,
      bottom_type=WellBottomType.V,
      material_z_thickness=1,  # measured
      cross_section_type=CrossSectionType.CIRCLE,
    ),
  )
