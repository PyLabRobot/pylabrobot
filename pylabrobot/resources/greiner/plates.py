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

  # # # # # # # # # # Greiner_96_half_skirt_wellplate_200uL_vb # # # # # # # # # #


def greiner_96_wellplate_200uL_Vb(name: str, with_lid: bool = False) -> Plate:
  """Greiner cat. no.: 652260.
  SAPPHIRE MICROPLATE, 96 WELL, PP, FOR PCR, NATURAL, HALF SKIRT
  Spec: https://shop.gbo.com/en/usa/files/30114476/652260.pdf
  """
  return Plate(
    name=name,
    size_x=125.64,  # from spec (from bottom of skirt)
    size_y=85.98,  # from spec
    size_z=21.08 + (10.11-7.57) - 0.22,  # measured (well height + wall height - well_protruding_height); see PR#950
    lid=None,
    model=greiner_96_wellplate_200uL_Vb.__name__,
    plate_type="semi-skirted",
    ordered_items=create_ordered_items_2d(
      Well,
      size_x=5.56,  # from spec (inner well width)
      size_y=5.56,  # from spec (inner well length)
      size_z=20.65,  # from spec
      dx=10.75,  # measured
      dy=8.5,  # measured
      dz=0,  # semi-skirted plate
      material_z_thickness=0.43,  # from spec
      item_dx=9,  # from spec
      item_dy=9,  # from spec
      num_items_x=12,  # from spec
      num_items_y=8,  # from spec
      cross_section_type=CrossSectionType.CIRCLE,
      bottom_type=WellBottomType.V,
      max_volume=200,  # from spec (0.2 mL)
    ),
  )
