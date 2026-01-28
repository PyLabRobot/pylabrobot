from typing import Optional

from pylabrobot.resources.height_volume_functions import (
  compute_height_from_volume_rectangle,
  compute_volume_from_height_rectangle,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def AGenBio_96_wellplate_Ub_2200ul(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  AGenBio Catalog No. P-2.2-SQG-96
  - Material: Polypropylene
  - Max. volume: 2200 uL
  """
  INNER_WELL_WIDTH = 8  # measured
  INNER_WELL_LENGTH = 8  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    "size_y": INNER_WELL_LENGTH,  # measured
    "size_z": 38.2,  # measured to bottom of well
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.RECTANGLE,
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume,
      INNER_WELL_LENGTH,
      INNER_WELL_WIDTH,
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height,
      INNER_WELL_LENGTH,
      INNER_WELL_WIDTH,
    ),
    "material_z_thickness": 1,
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=42.5,  # from spec
    lid=lid,
    model=AGenBio_96_wellplate_Ub_2200ul.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=9.38,  # measured
      dy=6.24,  # measured
      dz=3.8,  # measured
      item_dx=9,
      item_dy=9,
      **well_kwargs,
    ),
  )


def AGenBio_4_troughplate_75000uL_Vb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  AGenBio Catalog No. RES-75-4MW
  - Material: Polypropylene
  - Max. volume: 75 mL
  """
  INNER_WELL_WIDTH = 26.1  # measured
  INNER_WELL_LENGTH = 71.2  # measured

  well_kwargs = {
    "size_x": 26,  # measured
    "size_y": 71.2,  # measured
    "size_z": 42.55,  # measured to bottom of well
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.RECTANGLE,
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume,
      INNER_WELL_LENGTH,
      INNER_WELL_WIDTH,
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height,
      INNER_WELL_LENGTH,
      INNER_WELL_WIDTH,
    ),
    "material_z_thickness": 1,
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=43.80,  # measured
    lid=lid,
    model=AGenBio_4_troughplate_75000uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=4,
      num_items_y=1,
      dx=9.8,  # measured
      dy=7.2,  # measured
      dz=0.9,  # measured
      item_dx=INNER_WELL_WIDTH + 1,  # 1 mm wall thickness
      item_dy=INNER_WELL_LENGTH,
      **well_kwargs,
    ),
  )


def AGenBio_4_troughplate_75000_Vb(name: str, lid: Optional[Lid] = None) -> Plate:
  """Deprecated. Use :func:`AGenBio_4_troughplate_75000uL_Vb` instead."""
  import warnings

  warnings.warn(
    "AGenBio_4_troughplate_75000_Vb is deprecated. Use AGenBio_4_troughplate_75000uL_Vb instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return AGenBio_4_troughplate_75000uL_Vb(name=name, lid=lid)


def AGenBio_1_wellplate_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """Deprecated. Use :func:`AGenBio_1_troughplate_190000uL_Fl` instead."""
  import warnings

  warnings.warn(
    "AGenBio_1_wellplate_Fl is deprecated. Use AGenBio_1_troughplate_190000uL_Fl instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return AGenBio_1_troughplate_190000uL_Fl(name=name, lid=lid)


def AGenBio_1_troughplate_190000uL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  AGenBio Catalog No. RES-190-F
  - Material: Polypropylene
  - Max. volume: 190 mL
  """
  INNER_WELL_WIDTH = 107.2  # measured
  INNER_WELL_HEIGHT = 70.9  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    "size_y": INNER_WELL_HEIGHT,  # measured
    "size_z": 24.76,  # measured to bottom of well
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.RECTANGLE,
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume,
      INNER_WELL_HEIGHT,
      INNER_WELL_WIDTH,
    ),
    "compute_volume_from_height": lambda liquid_height: compute_volume_from_height_rectangle(
      liquid_height,
      INNER_WELL_HEIGHT,
      INNER_WELL_WIDTH,
    ),
    "material_z_thickness": 1,
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=44.2,  # measured
    lid=lid,
    model=AGenBio_1_troughplate_190000uL_Fl.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=10.1,
      dy=7.6,
      dz=5.88,
      item_dx=INNER_WELL_WIDTH,
      item_dy=INNER_WELL_HEIGHT,
      **well_kwargs,
    ),
  )


def AGenBio_1_troughplate_100000uL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  AGenBio Catalog No. RES-100-F
  - Material: Polypropylene
  - Max. volume: 100 mL
  """
  INNER_WELL_WIDTH = 107.2  # measured
  INNER_WELL_HEIGHT = 70.9  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    "size_y": INNER_WELL_HEIGHT,  # measured
    "size_z": 13,  # measured to bottom of well
    "bottom_type": WellBottomType.FLAT,
    "cross_section_type": CrossSectionType.RECTANGLE,
    "compute_height_from_volume": lambda liquid_volume: compute_height_from_volume_rectangle(
      liquid_volume,
      INNER_WELL_HEIGHT,
      INNER_WELL_WIDTH,
    ),
    "material_z_thickness": 1,
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=31.4,  # from spec
    lid=lid,
    model=AGenBio_1_troughplate_100000uL_Fl.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=9.8,
      dy=7.6,
      dz=5.88,
      item_dx=INNER_WELL_WIDTH,
      item_dy=INNER_WELL_HEIGHT,
      **well_kwargs,
    ),
  )
