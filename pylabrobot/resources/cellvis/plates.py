import warnings

from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import (
  CrossSectionType,
  Well,
  WellBottomType,
)


def cellvis_24_wellplate_3600uL_Fb_lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.15,  # from spec
    size_y=85.05,  # from spec
    size_z=10,  # from spec
    nesting_z_height=7.5,  # from spec
    model="cellvis_24_wellplate_3600uL_Fb_lid",
  )


def cellvis_24_wellplate_3600uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """p/n P24-1.5P

  https://www.cellvis.com/_24-well-plate-with--number-1.5-glass-like-polymer-coverslip-bottom-tissue-culture-treated-for-better-cell-attachment-than-cover-glass_/product_detail.php?product_id=65
  """

  return Plate(
    name=name,
    size_x=127.5,  # from spec
    size_y=85.25,  # from spec
    size_z=20,  # from spec
    lid=cellvis_24_wellplate_3600uL_Fb_lid(name + "_Lid") if with_lid else None,
    model="cellvis_24_wellplate_3600uL_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=6,
      num_items_y=4,
      # dx=17.05 - (15.54 / 2),  # from spec
      # dy=13.67 - (15.54 / 2),  # from spec
      dx=17.05 - (16.2 / 2),  # from spec
      dy=13.67 - (16.2 / 2),  # from spec
      dz=1 - 0.25,  # from spec
      item_dx=19.3,  # from spec
      item_dy=19.3,  # from spec
      size_x=15.54,  # from spec
      size_y=15.54,  # from spec
      size_z=19,  # from spec
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.25,
      cross_section_type=CrossSectionType.CIRCLE,
      name_prefix=name,
    ),
  )


def cellvis_96_wellplate_350uL_Fb_lid(name: str) -> Lid:
  return Lid(
    name=name,
    size_x=127.6,  # from spec
    size_y=85.75,  # from spec
    size_z=10,  # from spec
    nesting_z_height=10 - (16.03 - 13.83),  # from spec
    model="cellvis_96_wellplate_350uL_Fb_lid",
  )


def cellvis_96_wellplate_350uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """p/n P96-1.5H-N

  https://www.cellvis.com/_96-well-glass-bottom-plate-with-high-performance-number-1.5-cover-glass_/product_detail.php?product_id=50
  """

  return Plate(
    name=name,
    size_x=127.6,  # from spec
    size_y=85.75,  # from spec
    size_z=13.83,  # from spec
    lid=cellvis_96_wellplate_350uL_Fb_lid(name + "_Lid") if with_lid else None,
    model="cellvis_96_wellplate_350uL_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.3 - (6.8 / 2),  # from spec
      dy=11.36 - (6.8 / 2),  # from spec
      dz=13.83 - 11.93,  # from spec
      item_dx=9,  # from spec
      item_dy=9,  # from spec
      size_x=6.8,  # from spec
      size_y=6.8,  # from spec
      size_z=11.93,  # from spec
      bottom_type=WellBottomType.FLAT,
      material_z_thickness=0.17,  # +- 0.005mm
      cross_section_type=CrossSectionType.CIRCLE,
      name_prefix=name,
    ),
  )


def cellvis_384_wellplate_120uL_Fb_lid(name: str) -> Lid:
  """Create the lid supplied with Cellvis P384-1.5H-N.

  Manufacturer drawing:
  https://www.cellvis.com/images/glass_bottom_plate_384_well_size.png
  """
  return Lid(
    name=name,
    size_x=127.15,  # from spec
    size_y=85.05,  # from spec
    size_z=10,  # from spec
    nesting_z_height=10 - (16.8 - 14.33),  # from spec
    model="cellvis_384_wellplate_120uL_Fb_lid",
  )


def cellvis_384_wellplate_120uL_Fb(name: str, with_lid: bool = False) -> Plate:
  """Create a Cellvis P384-1.5H-N glass-bottom plate, 120 uL per well.

  Black polystyrene frame with #1.5H cover glass (0.170 +/- 0.005 mm).
  Square wells taper from 3.7 mm at the opening to 3.3 mm at the bottom;
  the well bounding box uses the opening width. No calibrated height-volume
  conversion is provided. Plate dimensions have been physically verified.

  Manufacturer specifications:
  https://www.cellvis.com/_384-well-glass-bottom-plate-with-high-performance-number-1.5-cover-glass_/product_detail.php?product_id=53
  Manufacturer drawing:
  https://www.cellvis.com/images/glass_bottom_plate_384_well_size.png

  Args:
    name: Name of the plate.
    with_lid: Whether to include the supplied lid.

  Returns:
    The plate with 24 columns and 16 rows of flat-bottom wells.
  """
  return Plate(
    name=name,
    size_x=127.6,  # from spec
    size_y=85.6,  # from spec
    size_z=14.33,  # from spec
    lid=cellvis_384_wellplate_120uL_Fb_lid(name + "_Lid") if with_lid else None,
    model="cellvis_384_wellplate_120uL_Fb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,  # from spec
      num_items_y=16,  # from spec
      dx=12.05 - 3.7 / 2,  # from spec: leftmost well center to bounding-box origin
      dy=9.05 - 3.7 / 2,  # from spec: frontmost well center to bounding-box origin
      dz=2.78,  # from spec: underside of cover glass above plate base
      item_dx=4.5,  # from spec
      item_dy=4.5,  # from spec
      size_x=3.7,  # from spec: opening width; bottom width is 3.3 mm
      size_y=3.7,  # from spec
      size_z=11.38 + 0.17,  # from spec: cavity depth plus cover glass thickness
      material_z_thickness=0.17,  # from spec: +/- 0.005 mm
      max_volume=120,  # from spec: 0.12 mL
      bottom_type=WellBottomType.FLAT,  # from spec
      cross_section_type=CrossSectionType.RECTANGLE,  # from spec
      name_prefix=name,
    ),
  )


# --------------------------------------------------------------------------- #
# Deprecated function names (backward compatibility)
# --------------------------------------------------------------------------- #


def CellVis_24_wellplate_3600uL_Fb_Lid(name: str) -> Lid:  # remove v1b1
  """Deprecated alias for cellvis_24_wellplate_3600uL_Fb_lid().

  This alias will be removed in v1b1.
  Use `cellvis_24_wellplate_3600uL_Fb_lid()` instead.
  """
  warnings.warn(
    "CellVis_24_wellplate_3600uL_Fb_Lid() is deprecated and will be removed in v1b1. "
    "Use cellvis_24_wellplate_3600uL_Fb_lid() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cellvis_24_wellplate_3600uL_Fb_lid(name)


def CellVis_24_wellplate_3600uL_Fb(name: str, with_lid: bool = False) -> Plate:  # remove v1b1
  """Deprecated alias for cellvis_24_wellplate_3600uL_Fb().

  This alias will be removed in v1b1.
  Use `cellvis_24_wellplate_3600uL_Fb()` instead.
  """
  warnings.warn(
    "CellVis_24_wellplate_3600uL_Fb() is deprecated and will be removed in v1b1. "
    "Use cellvis_24_wellplate_3600uL_Fb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cellvis_24_wellplate_3600uL_Fb(name, with_lid)


def CellVis_96_wellplate_350uL_Fb_Lid(name: str) -> Lid:  # remove v1b1
  """Deprecated alias for cellvis_96_wellplate_350uL_Fb_lid().

  This alias will be removed in v1b1.
  Use `cellvis_96_wellplate_350uL_Fb_lid()` instead.
  """
  warnings.warn(
    "CellVis_96_wellplate_350uL_Fb_Lid() is deprecated and will be removed in v1b1. "
    "Use cellvis_96_wellplate_350uL_Fb_lid() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cellvis_96_wellplate_350uL_Fb_lid(name)


def CellVis_96_wellplate_350uL_Fb(name: str, with_lid: bool = False) -> Plate:  # remove v1b1
  """Deprecated alias for cellvis_96_wellplate_350uL_Fb().

  This alias will be removed in v1b1.
  Use `cellvis_96_wellplate_350uL_Fb()` instead.
  """
  warnings.warn(
    "CellVis_96_wellplate_350uL_Fb() is deprecated and will be removed in v1b1. "
    "Use cellvis_96_wellplate_350uL_Fb() instead.",
    DeprecationWarning,
    stacklevel=2,
  )
  return cellvis_96_wellplate_350uL_Fb(name, with_lid)
