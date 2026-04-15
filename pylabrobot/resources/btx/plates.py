# https://btxonline.com/media/wysiwyg/tab_content/BTX-Electroporation-Multiwell-Plates-25.pdf
# https://support.btxonline.com/hc/en-us/article_attachments/6352046003987

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

_btx_96_wellplate_125ul_Fb_2mm_height_volume_data = {
  0.0: 0.0,
  10.0: 160.0,  # measured brim volume at approximately 10 mm liquid height
}


def BTX_96_wellplate_125ul_Fb_2mm(name: str) -> Plate:
  """BTX 96-well disposable electroporation plate, 2 mm gap.

  - BTX part no.: 45-0450 / 45-0450-M
  - Bottom type: flat
  - Nominal / manufacturer max-use volume: 125 uL
  - Recommended working volume: 50-100 uL
  - Approximate physical brim volume: 160 uL

  Notes:
  - The well free aperture is modeled as approximately 2.0 mm x 8.0 mm based on BTX gap size,
    BTX published footprint/pitch, and manual measurements.
  - Internal electrode termination is approximately 1 mm above the cavity floor, but that is not
    represented as an internal obstacle in the standard PLR Well model.
  - dx, dy, dz, well dimensions, and material_z_thickness are partly inferred from BTX published
    dimensions plus manual measurements and should be treated as provisional.
  """

  well_kwargs = {
    "size_x": 2.0,  # effective free aperture between electrodes, inferred from BTX gap size
    "size_y": 8.0,  # measured/inferred from row wall spacing
    "size_z": 10.8,  # 10.0 mm internal height + 0.8 mm bottom thickness, inferred
    "bottom_type": WellBottomType.FLAT,
    "material_z_thickness": 0.8,  # inferred from side profile measurements
    "cross_section_type": CrossSectionType.RECTANGLE,
    "max_volume": 160,  # measured brim volume; BTX nominal volume is 125 uL
    "height_volume_data": _btx_96_wellplate_125ul_Fb_2mm_height_volume_data,
  }

  return Plate(
    name=name,
    size_x=127.8,  # from BTX spec
    size_y=85.5,  # from BTX spec
    size_z=15.9,  # from BTX spec
    lid=None,
    model=BTX_96_wellplate_125ul_Fb_2mm.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=13.3,  # measured manually
      dy=7.0,  # measured manually
      dz=4.0,  # measured manually
      item_dx=9.0,
      item_dy=9.0,
      **well_kwargs,
    ),
  )
