""" Corning-Falcon Plates"""

from typing import Optional

from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_conical_frustum,
  compute_volume_from_height_conical_frustum,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


# # # # # # # # # # Cor_Falcon_96_wellplate_275ul_Fb # # # # # # # # # #

def Cor_Falcon_96_wellplate_275ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.: 353072
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/
    Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/353072
  - brand: Falcon
  - material: Polypropylene
  - max_volume: 275 uL
  """
  BOTTOM_RADIUS = 3.175
  TOP_RADIUS = 3.425

  return Plate(
    name=name,
    size_x=127.76,  # directly from reference manual
    size_y=85.11,  # directly from reference manual
    size_z=14.30,  # without lid, directly from reference manual
    lid=lid,
    model=Cor_Falcon_96_wellplate_275ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.05,  # measured
      dy=7.75,  # measured
      dz=1.11,  # from reference manual
      item_dx=8.99,
      item_dy=8.99,
      size_x=6.35,
      size_y=6.35,
      size_z=14.30,
      bottom_type=WellBottomType.FLAT,
      compute_volume_from_height=lambda liquid_height: compute_volume_from_height_conical_frustum(
        liquid_height, BOTTOM_RADIUS, TOP_RADIUS
      ),
      compute_height_from_volume=lambda liquid_volume: compute_height_from_volume_conical_frustum(
        liquid_volume, BOTTOM_RADIUS, TOP_RADIUS
      ),
    ),
  )


# # # # # # # # # # Cor_Falcon_96_wellplate_250ul_Rb # # # # # # # # # #

def Cor_Falcon_96_wellplate_250ul_Rb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.: 353077
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/
    Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/
    falcon96WellPolystyreneMicroplates
  - brand: Falcon
  - distributor: (Fisher Scientific, 08-772-17)
  - material: Polystyrene
  - tc_treated: Yes
  - sterile: yes
  - tech_drawing: https://www.corning.com/catalog/cls/documents/drawings/LSR00181.pdf
  - max_volume: 250 uL
  """
  TOP_INNER_WELL_RADIUS = 3.425
  BOTTOM_INNER_WELL_RADIUS = 3.175

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 14.30,  # from spec
    "bottom_type": WellBottomType.U,
    "max_volume": 0.25,  # from spec
  }

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.11,
    size_z=14.30,
    lid=lid,
    model=Cor_Falcon_96_wellplate_250ul_Rb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.38 - TOP_INNER_WELL_RADIUS,  # from spec
      dy=11.39 - TOP_INNER_WELL_RADIUS,  # from spec
      dz=1.80,  # calibrated manually by z-stepping down using a pipette.
      item_dx=8.99,  # measured
      item_dy=8.99,  # measured
      **well_kwargs,
    ),
  )

def Cor_Falcon_96_wellplate_250ul_Rb_Lid(name: str) -> Lid:
  raise NotImplementedError("This lid is not currently defined.")


# # # # # # # # # # Cor_Falcon_96_wellplate_340ul_Fb_Black # # # # # # # # # #

def Cor_Falcon_96_wellplate_340ul_Fb_Black(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.: 353219
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/
    Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/353219
  - brand: Falcon
  - material: Polystyrene
  - tc_treated: Yes
  - tech_drawing: https://www.corning.com/catalog/cls/documents/drawings/LSR00181.pdf
  - max_volume: 340 uL
  """
  TOP_INNER_WELL_RADIUS = 6.96 / 2  # from spec
  BOTTOM_INNER_WELL_RADIUS = 6.58 / 2  # from spec

  well_kwargs = {
    "size_x": TOP_INNER_WELL_RADIUS * 2,  # from spec
    "size_y": TOP_INNER_WELL_RADIUS * 2,  # from spec
    "size_z": 10.90,  # from spec
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "material_z_thickness": 0.15,  # measured at 0.15 mm
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=14.40,  # from spec
    lid=lid,
    model=Falcon_96_wellplate_Fl_Black.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=10.7,  # calculated from spec, manually calibrated
      dy=7.7,  # calculated from spec, manually calibrated
      dz=6.7,  # calculated from spec, manually calibrated
      item_dx=9,
      item_dy=9,
      **well_kwargs,
    ),
  )


def Falcon_96_wellplate_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  raise NotImplementedError(
    "Falcon_96_wellplate_Fl definition is deprecated. Use "
    "Cor_Falcon_96_wellplate_340ul_Fb_Black instead."
  )

def Falcon_96_wellplate_Fl_Black(name: str, lid: Optional[Lid] = None) -> Plate:
  raise NotImplementedError(
    "Falcon_96_wellplate_Fl_Black definition is deprecated. Use "
    "Cor_Falcon_96_wellplate_340ul_Fb_Black instead."
  )
