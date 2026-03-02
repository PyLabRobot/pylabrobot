"""Greiner plates"""

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)

# # # # # # # # # # Greiner_384_wellplate_28ul_Fb # # # # # # # # # #


def _compute_volume_from_height_Greiner_384_wellplate_28ul_Fb(h: float) -> float:
  """Estimate liquid volume (µL) from observed liquid height (mm)
  in the Greiner 384 wellplate 28ul Fb, using piecewise linear interpolation.
  """
  raise NotImplementedError(
    "Computation of volume from height is not currently defined. "
    "It is difficult (perhaps impossible?) to robustly perform liquid "
    "level detection in such a small well AND such low volumes."
  )


def _compute_height_from_volume_Greiner_384_wellplate_28ul_Fb(volume_ul: float) -> float:
  """Estimate liquid height (mm) from known liquid volume (µL)
  in the Greiner 384 wellplate 28ul Fb, using piecewise linear interpolation.
  """
  raise NotImplementedError(
    "Computation of height from volume is not currently defined. "
    "It is difficult (perhaps impossible?) to robustly perform liquid "
    "level detection in such a small well AND such low volumes."
  )


def Greiner_384_wellplate_28ul_Fb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")


#: Greiner_384_wellplate_28ul_Fb
def Greiner_384_wellplate_28ul_Fb(name: str, with_lid: bool = False) -> Plate:
  """Greiner cat. no.: 784075.
  - Colour: white
  - alternative cat. no.: 784075-25: white; 784076, 784076-25: black; 784101: clear.
  - Material: Polystyrene
  - "shallow-well"
  - Sterilized: No
  - Autoclavable: No
  - Chemical resistance:?
  - Thermal resistance: ?
  - Surface treatment: non-treated
  - Sealing options: ?
  - Cleanliness: "Free of detectable DNase, RNase, human DNA"
  - Automation compatibility: not specifically declared
  - Total volume = 28 ul
  - URL: https://shop.gbo.com/en/england/products/bioscience/microplates/384-well-microplates/384-well-small-volume-hibase-microplates/784075.html
  """
  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=14.4,
    lid=Greiner_384_wellplate_28ul_Fb_Lid(name + "_lid") if with_lid else None,
    model=Greiner_384_wellplate_28ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=10.48 - 0.2,  # physical testing shows -0.2mm deviation from OEM technical drawing
      dy=7.34,
      dz=8.9,
      item_dx=4.5,
      item_dy=4.5,
      size_x=3.3,
      size_y=3.3,
      size_z=5.5,
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.9,
      cross_section_type=CrossSectionType.CIRCLE,
      # compute_volume_from_height=_compute_volume_from_height_Greiner_384_wellplate_28ul_Fb,
      # compute_height_from_volume=_compute_height_from_volume_Greiner_384_wellplate_28ul_Fb,
    ),
  )
