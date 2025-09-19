from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def BioER_96_wellplate_Vb_2200ul(name: str) -> Plate:
  """BioER Cat. No. BSH06M1T-A (KingFisher-compatible)
  Spec: https://en.bioer.com/uploadfiles/2024/05/20240513165756879.pdf
  """
  return Plate(
    name=name,
    size_x=127.1,  # from spec
    size_y=85.0,  # from spec
    size_z=44.2,  # from spec
    lid=None,
    model=BioER_96_wellplate_Vb_2200ul.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=8.25,  # from spec (inner well width)
      size_y=8.25,  # from spec (inner well length)
      size_z=42.4,  # measured (well depth)
      dx=9.5,  # measured (column pitch)
      dy=7.5,  # measured (row pitch)
      dz=6,  # measured (expected to be 44.2-42.4-0.8=1, but 6 optimal on Hamilton_MFX_plateholder_DWP_metal_tapped  )
      material_z_thickness=0.8,  # measured
      item_dx=9.0,  # measured
      item_dy=9.0,  # measured
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,
      bottom_type=WellBottomType.V,
      max_volume=2200,  # from spec (2.2 mL)
    ),
  )
