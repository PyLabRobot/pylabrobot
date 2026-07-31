import warnings
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


def agenbio_96_wellplate_Ub_2200uL(name: str, lid: Optional[Lid] = None) -> Plate:
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
    model=agenbio_96_wellplate_Ub_2200uL.__name__,
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


def agenbio_4_troughplate_75mL_Vb(name: str, lid: Optional[Lid] = None) -> Plate:
  """
  AGenBio Catalog No. RES-75-4MW
  - Material: Polypropylene
  - Max. volume: 75 mL
  """
  INNER_WELL_WIDTH = 26.1  # measured
  INNER_WELL_LENGTH = 72.0  # corrected from 71.2 to seat 8 channels at STAR's 9mm spacing

  well_kwargs = {
    "size_x": 26,  # measured
    "size_y": INNER_WELL_LENGTH,
    "size_z": 42.55,  # measured to bottom of well
    "max_volume": 75_000,  # spec rating; box volume (~80 mL) would overstate capacity
    "bottom_type": WellBottomType.V,
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
    model=agenbio_4_troughplate_75mL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=4,
      num_items_y=1,
      dx=9.8,  # measured
      dy=6.8,
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
  return agenbio_4_troughplate_75mL_Vb(name=name, lid=lid)


def AGenBio_1_wellplate_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """Deprecated. Use :func:`AGenBio_1_troughplate_190000uL_Fl` instead."""
  import warnings

  warnings.warn(
    "AGenBio_1_wellplate_Fl is deprecated. Use AGenBio_1_troughplate_190000uL_Fl instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return agenbio_1_troughplate_190mL_Fl(name=name, lid=lid)


def agenbio_1_troughplate_190mL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """AGenBio single-well reagent reservoir, 190 mL, flat-bottom (ANSI/SLAS footprint).

  cat. no.: RES-190-F
  Material: polypropylene
  """
  INNER_WELL_WIDTH = 107.2  # measured
  INNER_WELL_HEIGHT = 70.9  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    "size_y": INNER_WELL_HEIGHT,  # measured
    "size_z": 44.2 - 5.88,  # well cavity reaches the plate top (plate size_z 44.2 - dz 5.88)
    "max_volume": 190_000,  # spec rating; size_z reaches the rim so the box volume would overstate capacity
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
    model=agenbio_1_troughplate_190mL_Fl.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=10.1,
      dy=7.6,
      dz=5.88,
      item_dx=None,  # single column
      item_dy=None,  # single row
      **well_kwargs,
    ),
  )


def agenbio_1_troughplate_100mL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:
  """AGenBio single-well reagent reservoir, 100 mL, flat-bottom (ANSI/SLAS footprint).

  cat. no.: RES-100-F
  Material: polypropylene
  """
  INNER_WELL_WIDTH = 107.2  # measured
  INNER_WELL_HEIGHT = 70.9  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    "size_y": INNER_WELL_HEIGHT,  # measured
    "size_z": 31.4 - 5.88,  # well cavity reaches the plate top (plate size_z 31.4 - dz 5.88)
    "max_volume": 100_000,  # spec rating; size_z reaches the rim so the box volume would overstate capacity
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
    size_z=31.4,  # from spec
    lid=lid,
    model=agenbio_1_troughplate_100mL_Fl.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=1,
      num_items_y=1,
      dx=9.8,
      dy=7.6,
      dz=5.88,
      item_dx=None,  # single column
      item_dy=None,  # single row
      **well_kwargs,
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def AGenBio_1_troughplate_100000uL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:  # remove v1b1
  """Deprecated alias for agenbio_1_troughplate_100mL_Fl().

  This alias will be removed in v1b1.
  Use `agenbio_1_troughplate_100mL_Fl()` instead.
  """
  warnings.warn(
    "AGenBio_1_troughplate_100000uL_Fl() is deprecated and will be removed in v1b1. "
    "Use agenbio_1_troughplate_100mL_Fl() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return agenbio_1_troughplate_100mL_Fl(name, lid)


def AGenBio_1_troughplate_190000uL_Fl(name: str, lid: Optional[Lid] = None) -> Plate:  # remove v1b1
  """Deprecated alias for agenbio_1_troughplate_190mL_Fl().

  This alias will be removed in v1b1.
  Use `agenbio_1_troughplate_190mL_Fl()` instead.
  """
  warnings.warn(
    "AGenBio_1_troughplate_190000uL_Fl() is deprecated and will be removed in v1b1. "
    "Use agenbio_1_troughplate_190mL_Fl() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return agenbio_1_troughplate_190mL_Fl(name, lid)


def AGenBio_4_troughplate_75000uL_Vb(name: str, lid: Optional[Lid] = None) -> Plate:  # remove v1b1
  """Deprecated alias for agenbio_4_troughplate_75mL_Vb().

  This alias will be removed in v1b1.
  Use `agenbio_4_troughplate_75mL_Vb()` instead.
  """
  warnings.warn(
    "AGenBio_4_troughplate_75000uL_Vb() is deprecated and will be removed in v1b1. "
    "Use agenbio_4_troughplate_75mL_Vb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return agenbio_4_troughplate_75mL_Vb(name, lid)


def AGenBio_96_wellplate_Ub_2200ul(name: str, lid: Optional[Lid] = None) -> Plate:  # remove v1b1
  """Deprecated alias for agenbio_96_wellplate_Ub_2200uL().

  This alias will be removed in v1b1.
  Use `agenbio_96_wellplate_Ub_2200uL()` instead.
  """
  warnings.warn(
    "AGenBio_96_wellplate_Ub_2200ul() is deprecated and will be removed in v1b1. "
    "Use agenbio_96_wellplate_Ub_2200uL() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return agenbio_96_wellplate_Ub_2200uL(name, lid)
