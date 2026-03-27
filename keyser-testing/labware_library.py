"""Custom labware definitions for our lab.

These are modified versions of standard plates/tips that match our
specific hardware configuration.
"""

from typing import Optional

from pylabrobot.resources import CrossSectionType, Well
from pylabrobot.resources.tecan.plates import TecanPlate
from pylabrobot.resources.tecan.tip_creators import TecanTip, TipType
from pylabrobot.resources.tecan.tip_racks import TecanTipRack
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import WellBottomType


# ============== Plates ==============


# Volume/height functions from Eppendorf_96_wellplate_250ul_Vb
def _compute_volume_from_height(h: float) -> float:
  if h > 19.5:
    raise ValueError(f"Height {h} is too large for Eppendorf_96_wellplate_250ul_Vb_skirted")
  return max(
    0.89486648 + 2.92455131 * h + 2.03472797 * h**2 + -0.16509371 * h**3 + 0.00675759 * h**4,
    0,
  )


def _compute_height_from_volume(liquid_volume: float) -> float:
  if liquid_volume > 262.5:
    raise ValueError(
      f"Volume {liquid_volume} is too large for Eppendorf_96_wellplate_250ul_Vb_skirted"
    )
  return max(
    0.118078503
    + 0.133333914 * liquid_volume
    + -0.000802726227 * liquid_volume**2
    + 3.29761957e-06 * liquid_volume**3
    + -5.29119614e-09 * liquid_volume**4,
    0,
  )


def Eppendorf_96_wellplate_250ul_Vb_skirted(name: str) -> TecanPlate:
  """Eppendorf twin.tec 96-well PCR plate, 250uL, V-bottom.

  TecanPlate version with z-positions for LiHa operations, marked as skirted
  for compatibility with MP_3Pos carrier PlateHolder.

  Z-positions based on Microplate_96_Well from EVOware defaults.

  Eppendorf cat. no.: 0030133374
  """
  return TecanPlate(
    name=name,
    size_x=123.0,
    size_y=81.0,
    size_z=20.3,
    lid=None,
    model="Eppendorf_96_wellplate_250ul_Vb_skirted",
    z_start=1957.0,
    z_dispense=1975.0,
    z_max=2005.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=6.76,
      dy=8.26,
      dz=0.0,
      item_dx=9,
      item_dy=9,
      size_x=5.48,
      size_y=5.48,
      size_z=19.5,
      bottom_type=WellBottomType.V,
      material_z_thickness=1.2,
      cross_section_type=CrossSectionType.CIRCLE,
      compute_volume_from_height=_compute_volume_from_height,
      compute_height_from_volume=_compute_height_from_volume,
    ),
  )


# ============== Tips ==============


def DiTi_50ul_SBS_LiHa_Air_tip(name: Optional[str] = None) -> TecanTip:
  """DiTi 50uL SBS tip for Air LiHa.

  Tecan part no. 30057813.
  Tip length 58.1mm measured with calipers.
  """
  return TecanTip(
    name=name,
    has_filter=False,
    total_tip_length=58.1,
    maximal_volume=55.0,
    tip_type=TipType.AIRDITI,
  )


def DiTi_50ul_SBS_LiHa_Air(name: str) -> TecanTipRack:
  """DiTi 50uL SBS tip rack for Air LiHa.

  Tecan part no. 30057813. SBS-format tip box (~45mm tall).
  Based on DiTi_50ul_SBS_LiHa with corrected tip definition
  (proper total_tip_length and AIRDITI tip type).
  """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=86.0,
    size_z=45.0,
    model="DiTi_50ul_SBS_LiHa_Air",
    z_start=1360.0,
    z_dispense=1360.0,
    z_max=1660.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.0,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_SBS_LiHa_Air_tip,
    ),
  )
