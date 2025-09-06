from pylabrobot.resources import CrossSectionType, Plate, Well, WellBottomType
from pylabrobot.resources.utils import create_ordered_items_2d


def PerkinElmer_96_wellplate_400ul_Fb(name: str) -> Plate:
  """Creates a PerkinElmer 96 well plate with 400 µL wells.

  Part number 6005680, 6005688, 6005689

  http://per-form.hu/wp-content/uploads/2015/08/perkinelmer-Microplates.pdf
  """

  WELL_DIAMETER = 7.15

  well_kwargs = {
    "size_x": WELL_DIAMETER,
    "size_y": WELL_DIAMETER,
    "size_z": 10.80,
    "bottom_type": WellBottomType.FLAT,
    "material_z_thickness": 0.87,  # measured using ztouch probing, dependent on dz (caliper)
    "cross_section_type": CrossSectionType.CIRCLE,
    "max_volume": 400,
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.47,  # from spec
    size_z=14.60,  # from spec
    lid=None,
    model=PerkinElmer_96_wellplate_400ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.38 - WELL_DIAMETER / 2,  # from spec
      dy=11.24 - WELL_DIAMETER / 2,  # from spec
      dz=2.88,  # caliper
      item_dx=9,
      item_dy=9,
      **well_kwargs,
    ),
  )
