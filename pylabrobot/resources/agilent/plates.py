from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import Well, WellBottomType


def agilent_96_wellplate_150uL_Ub(name: str) -> Plate:
  """
  Part number: 5042-8502

  https://www.agilent.com/cs/library/datasheets/public/ds-well-plate-specifications-5994-6035en-agilent.pdf
  """

  diameter = 6.4  # from spec

  well_kwargs = {
    "size_x": diameter,  # from spec
    "size_y": diameter,  # from spec
    "size_z": 14.0,  # from spec
    "bottom_type": WellBottomType.U,
    "material_z_thickness": 0.88,  # measured using z-probing
    "max_volume": 150,
  }

  return Plate(
    name=name,
    size_x=127.8,  # standard
    size_y=85.5,  # standard
    size_z=15.9,  # from spec
    lid=None,
    model=agilent_96_wellplate_150uL_Ub.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - diameter / 2,  # from spec
      dy=11.2 - diameter / 2,  # from spec
      dz=16.0 - 14.0 - 0.88,  # spec - spec - measured
      item_dx=9.0,  # standard
      item_dy=9.0,  # standard
      **well_kwargs,
    ),
    plate_type="skirted",
  )
