"""Corning-Costar plates."""

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

# # # # # # # # # # Cor_Cos_6_wellplate_16800ul_Fb # # # # # # # # # #


def Cor_Cos_6_wellplate_16800ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.s: 3335, 3506, 3516, 3471
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/costarMultipleWellCellCulturePlates
  - brand: Costar
  - distributor: (Fisher Scientific, 10234832)
  - material: Polystyrene
  - tech_drawing: tech_drawings/Cor_Cos_6_wellplate_16800ul_Fb.pdf
  """
  BOTTOM_INNER_WELL_RADIUS = 35.43 / 2  # from Corning Product Description
  TOP_INNER_WELL_RADIUS = 34.80 / 2  # from Corning Product Description

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 17.4,  # from Corning Product Description
    "bottom_type": WellBottomType.FLAT,
    "max_volume": 16800,  # from Corning Product Description
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
  }

  return Plate(
    name=name,
    size_x=127.76,
    size_y=85.47,
    size_z=20.27,
    lid=lid,
    model=Cor_Cos_6_wellplate_16800ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=3,
      num_items_y=2,
      dx=24.76 - BOTTOM_INNER_WELL_RADIUS,  # from Corning Product Description
      dy=23.16 - BOTTOM_INNER_WELL_RADIUS,  # from Corning Product Description
      dz=2.54,  # from Corning Product Description
      item_dx=39.12,  # from Corning Product Description
      item_dy=39.12,  # from Corning Product Description
      material_z_thickness=1.27,  # from Corning Product Description
      **well_kwargs,
    ),
  )


def Cor_Cos_6_wellplate_16800ul_Fb_Lid(name: str) -> Lid:
  """
  - brand: Costar
  """
  return Lid(
    name=name,
    size_x=127.0,
    size_y=86.0,
    size_z=7.8,
    nesting_z_height=6.7,  # measure overlap between lid and plate
    model="Cor_Cos_6_wellplate_16800ul_Fb_Lid",
  )


# # # # # # # # # # Cor_12_wellplate_6900ul_Fb # # # # # # # # # #


def Cor_Cos_12_wellplate_6900ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.s: 3336, 3512, 3513
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3336
  - brand: Costar
  - distributor: (Fisher Scientific, 10739864)
  - material: Polystyrene
  - tc_treated: Yes
  - tech_drawing: tech_drawings/Cor_Cos_24_wellplate_3470ul_Fb.pdf
  - notes: not validated
  """
  BOTTOM_INNER_WELL_RADIUS = 22.73 / 2  # from Corning Product Description
  TOP_INNER_WELL_RADIUS = 22.11 / 2  # from Corning Product Description

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 17.5,  # from Corning Product Description
    "bottom_type": WellBottomType.FLAT,
    "max_volume": 6900,  # from Corning Product Description
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
  }

  return Plate(
    name=name,
    size_x=127.76,  # from Corning Product Description
    size_y=85.6,  # from Corning Product Description
    size_z=20.02,  # from Corning Product Description
    lid=lid,
    model=Cor_Cos_12_wellplate_6900ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=4,
      num_items_y=3,
      dx=24.94 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dy=16.79 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dz=2.16,  # from Corning Product Description
      item_dx=26.01,  # from Corning Product Description
      item_dy=26.01,  # from Corning Product Description
      material_z_thickness=1.27,  # from Corning Product Description
      **well_kwargs,
    ),
  )


# # # # # # # # # # Cor_24_wellplate_3470ul_Fb # # # # # # # # # #


def Cor_Cos_24_wellplate_3470ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.s: 3337, 3524, 3526, 3527
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3337
  - brand: Costar
  - material: Polystyrene
  - tc_treated: Yes
  - tech_drawing: tech_drawings/Cor_Cos_24_wellplate_3470ul_Fb.pdf
  """
  BOTTOM_INNER_WELL_RADIUS = 16.26 / 2  # from Corning Product Description
  TOP_INNER_WELL_RADIUS = 15.62 / 2  # from Corning Product Description

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 17.4,  # from Corning Product Description
    "bottom_type": WellBottomType.FLAT,
    "max_volume": 3400,  # website
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
  }

  return Plate(
    name=name,
    size_x=127.76,  # from Corning Product Description
    size_y=85.47,  # from Corning Product Description
    size_z=20.27,  # from Corning Product Description
    lid=lid,
    model=Cor_Cos_24_wellplate_3470ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      dx=17.46 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dy=13.78 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dz=2.54,  # from Corning Product Description
      item_dx=19.3,  # from Corning Product Description
      item_dy=19.3,  # from Corning Product Description
      material_z_thickness=1.27,  # from Corning Product Description
      **well_kwargs,
    ),
  )


# # # # # # # # # # Cor_Cos_48_wellplate_1620ul_Fb # # # # # # # # # #


def Cor_Cos_48_wellplate_1620ul_Fb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  Corning cat. no.s: 3548
  - manufacturer_link: https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3548
  - brand: Costar
  - distributor: (Fisher Scientific, 07-200-86)
  - material: Polystyrene
  - tc_treated: Yes
  - sterile: yes
  - notes:

    - not validated
  """
  BOTTOM_INNER_WELL_RADIUS = 11.56 / 2  # from Corning Product Description
  TOP_INNER_WELL_RADIUS = 11.05 / 2  # from Corning Product Description

  well_kwargs = {
    "size_x": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_y": BOTTOM_INNER_WELL_RADIUS * 2,
    "size_z": 17.4,  # from Corning Product Description
    "bottom_type": WellBottomType.FLAT,
    "max_volume": 1620,  # from Corning Product Description
    "cross_section_type": CrossSectionType.CIRCLE,
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_conical_frustum(
      liquid_height, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_conical_frustum(
      liquid_volume, BOTTOM_INNER_WELL_RADIUS, TOP_INNER_WELL_RADIUS
    ),
  }

  return Plate(
    name=name,
    size_x=127.76,  # from Corning Product Description
    size_y=85.6,  # from Corning Product Description
    size_z=20.02,  # from Corning Product Description
    lid=lid,
    model=Cor_Cos_48_wellplate_1620ul_Fb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=8,
      num_items_y=6,
      dx=18.16 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dy=10.08 - TOP_INNER_WELL_RADIUS,  # from Corning Product Description
      dz=2.87,  # from Corning Product Description
      item_dx=13.08,  # from Corning Product Description
      item_dy=13.08,  # from Corning Product Description
      material_z_thickness=1.27,  # from Corning Product Description
      **well_kwargs,
    ),
  )
