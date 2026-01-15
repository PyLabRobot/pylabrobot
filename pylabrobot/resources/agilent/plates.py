import warnings

from pylabrobot.resources.height_volume_functions import (
  calculate_liquid_height_container_1segment_round_vbottom,
  calculate_liquid_volume_container_1segment_round_vbottom,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import CrossSectionType, Well, WellBottomType


def _compute_volume_from_height_agilent_96_wellplate_150uL_Vb(
  h: float,
):
  # well depth: 12.5 mm
  # well diameter at the top: 6.0 mm
  if h > 12.5:
    raise ValueError(f"Height {h} is too large for agilent_96_wellplate_150uL_Vb")
  return calculate_liquid_volume_container_1segment_round_vbottom(
    d=6.4, h_pyramid=12.5, liquid_height=h
  )


def _compute_height_from_volume_agilent_96_wellplate_150uL_Vb(
  v: float,
):
  if v > 150:
    raise ValueError(f"Volume {v} is too large for agilent_96_wellplate_150uL_Vb")
  return calculate_liquid_height_container_1segment_round_vbottom(
    d=6.4, h_pyramid=12.5, liquid_volume=v
  )


def agilent_96_wellplate_150uL_Vb(name: str) -> Plate:
  """
  Part number: 5042-8502

  Well Number                  | 96
  Well Form                    | Round
  Well Diameter (A)            | 6.4 mm
  Well Bottom Shape            | Conical
  Well Volume                  | 150 µL
  Well Working Volume          | 120 µL
  Well Depth (B)               | 14.0 mm
  Plate Height (C)             | 15.9 mm
  Plate Width (D)              | 85.5 mm
  Plate Length (E)             | 127.8 mm
  Row Distance (F)             | 9 mm
  Column Distance (G)          | 9 mm
  Row Offset (H)               | 11.2 mm
  Column Offset (I)            | 14.4 mm
  Frame Footprint              | SBS
  Frame Numbering              | Alphanumeric
  Skirted                      | Yes
  Color                        | Clear
  Material                     | Well: polypropylene
                               | Frame: polycarbonate
  Temperature Range            | -80 to 120 °C
  Autoclavable                 | Yes
  Sterile                      | Nonsterile
  Stackable                    | Yes
  Pack Size                    | 25, p/n 5042-8502
  Compatible Closing Mat       | p/n 5067-5154 (not recommended for
                               | chromatography autosamplers)
  Agilent Instrument Definition| Not applicable

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
    "compute_volume_from_height": _compute_volume_from_height_agilent_96_wellplate_150uL_Vb,
    "compute_height_from_volume": _compute_height_from_volume_agilent_96_wellplate_150uL_Vb,
  }

  return Plate(
    name=name,
    size_x=127.8,  # standard
    size_y=85.5,  # standard
    size_z=15.9,  # from spec
    lid=None,
    model=agilent_96_wellplate_150uL_Vb.__name__,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.4 - diameter / 2,  # from spec
      dy=11.2 - diameter / 2,  # from spec
      dz=15.9 - 14.0 - 0.88,  # spec - spec - measured manually
      item_dx=9.0,  # standard
      item_dy=9.0,  # standard
      **well_kwargs,
    ),
    plate_type="skirted",
  )


def agilent_96_wellplate_150uL_Ub(name: str) -> Plate:
  """Deprecated for agilent_96_wellplate_150uL_Vb. Use that one instead."""
  warnings.warn(
    "agilent_96_wellplate_150uL_Ub is deprecated. Use agilent_96_wellplate_150uL_Vb instead.",
    DeprecationWarning,
  )
  return agilent_96_wellplate_150uL_Vb(name)


def Agilent_2_reservoir_144mL_Vb(name: str) -> Plate:
  """Agilent 2 Reservoir 144mL V bottom
  Part Number: 203852-100
  - Max Volume: 144 mL
  Spec: https://www.agilent.com/cs/library/datasheets/public/ds-cell-analysis-5994-4426en-agilent.pdf
  """
  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.47,  # from spec
    size_z=44.04,  # from spec
    model="Agilent_2_reservoir_144mL_Vb",
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=2,  # from spec
      num_items_y=1,  # from spec
      dx=10.66,  # measured
      dy=7.12,  # measured
      dz=2.54,  # measured
      item_dx=54,  # from spec
      item_dy=0,  # from spec
      size_x=53.22,  # from spec
      size_y=71.23,  # from spec
      size_z=39.22,  # from spec
      bottom_type=WellBottomType.V,
      cross_section_type=CrossSectionType.RECTANGLE,
      material_z_thickness=1.15,
    ),
  )
