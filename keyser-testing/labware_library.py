"""Custom labware definitions for our lab.

These are modified versions of standard plates/tips that match our
specific hardware configuration.

Coordinate fixes applied (vs upstream pylabrobot definitions):
  - Plate dx corrected to match SBS/SLAS 4-2004 standard (P1=14.38mm)
  - MP_3Pos carrier off_x/off_y corrected from hardware calibration
"""

from typing import Optional

from pylabrobot.resources import Coordinate, CrossSectionType, Well
from pylabrobot.resources.carrier import PlateHolder, create_homogeneous_resources
from pylabrobot.resources.tecan.plate_carriers import TecanPlateCarrier
from pylabrobot.resources.tecan.plates import TecanPlate
from pylabrobot.resources.tecan.tip_creators import TecanTip, TipType
from pylabrobot.resources.tecan.tip_racks import TecanTipRack
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.well import WellBottomType


# ============== Carriers ==============


def MP_3Pos_Corrected(name: str) -> TecanPlateCarrier:
  """MP_3Pos carrier with corrected site locations from hardware calibration.

  Site X/Y/Z offsets corrected from EVOware carrier editor measurements.
  Per EVOware manual 9-44: Site X-Offset = distance from carrier left edge
  to labware left edge. Site Y-Offset = distance from carrier rear edge
  to labware rear edge.

  Upstream site X=5.5 is ~5.5mm too small. Corrected to 11.0.
  Upstream site Y values off by ~0.9mm. Corrected per EVOware measurements.
  Upstream site Z=62.5 corrected to 63.7 (carrier surface height).

  Tecan part no. 10612604.
  """
  return TecanPlateCarrier(
    name=name,
    size_x=149.0,
    size_y=316.0,
    size_z=62.5,
    off_x=12.0,
    off_y=24.7,
    roma_x=1670,
    roma_y=380,
    roma_z_safe=946,
    roma_z_end=2537,
    sites=create_homogeneous_resources(
      klass=PlateHolder,
      locations=[
        Coordinate(11.0, 14.4, 63.7),   # was (5.5, 13.5, 62.5)
        Coordinate(11.0, 109.4, 63.7),  # was (5.5, 109.5, 62.5)
        Coordinate(11.0, 205.4, 63.7),  # was (5.5, 205.5, 62.5)
      ],
      resource_size_x=127.0,
      resource_size_y=85.5,
      name_prefix=name,
      pedestal_size_z=0,
    ),
    model="MP_3Pos",
  )


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

  Well positions corrected to match SBS/SLAS 4-2004 standard:
    P1 = 14.38mm (A1 center X from left edge)
    dx = P1 - well_center_x = 14.38 - 2.74 = 11.64mm

  Eppendorf cat. no.: 0030133374
  """
  return TecanPlate(
    name=name,
    size_x=123.0,
    size_y=81.0,
    size_z=20.3,
    lid=None,
    model="Eppendorf_96_wellplate_250ul_Vb_skirted",
    z_start=300.0,     # taught: ~260-295, start slightly above plate top
    z_dispense=164.0,  # 99 + 65 (compensate for fitting_depth 4.9->11.0 correction)
    z_max=100.0,       # max depth (~10mm below dispense)
    area=33.2,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.64,   # SBS P1(14.38) - well_center(2.74); was 6.76
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
  Tip length 58.0mm, tip offset 53.1mm (fitting_depth 4.9mm).
  """
  return TecanTip(
    name=name,
    has_filter=False,
    total_tip_length=58.0,
    fitting_depth=11.0,
    maximal_volume=55.0,
    tip_type=TipType.AIRDITI,
  )


def DiTi_200ul_SBS_LiHa_Air_tip(name: Optional[str] = None) -> TecanTip:
  """DiTi 200uL SBS tip for Air LiHa.

  TODO: Measure actual tip length with calipers and update total_tip_length.
  """
  return TecanTip(
    name=name,
    has_filter=False,
    total_tip_length=58.5,
    fitting_depth=11.0,
    maximal_volume=220.0,
    tip_type=TipType.AIRDITI,
  )


def DiTi_1000ul_SBS_LiHa_Air_tip(name: Optional[str] = None) -> TecanTip:
  """DiTi 1000uL SBS tip for Air LiHa.

  TODO: Measure actual tip length with calipers and update total_tip_length.
  """
  return TecanTip(
    name=name,
    has_filter=False,
    total_tip_length=96.1,
    fitting_depth=11.0,
    maximal_volume=1100.0,
    tip_type=TipType.AIRDITI,
  )


# Tip extension: tip_ext = total_tip_length * 10 - fitting_depth * 10
# Used by jog UI to compensate Z readings when tips are mounted.
TIP_TYPES = {
  "none": {"label": "No tip", "tip_ext": 0},
  "50ul": {"label": "DiTi 50µL", "tip_ext": 470},    # 58.0 - 11.0 = 47.0mm
  "200ul": {"label": "DiTi 200µL", "tip_ext": 475},   # 58.5 - 11.0 = 47.5mm
  "1000ul": {"label": "DiTi 1000µL", "tip_ext": 851}, # 96.1 - 11.0 = 85.1mm
}


def DiTi_200ul_SBS_LiHa_Air(name: str) -> TecanTipRack:
  """DiTi 200uL SBS tip rack for Air LiHa.

  Based on DiTi_200ul_SBS_LiHa with AIRDITI tip type and corrected tip length.
  Z values TODO: teach from hardware.
  """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=86.8,
    size_z=30.0,
    model="DiTi_200ul_SBS_LiHa_Air",
    z_start=770.0,   # same box height as 50uL
    z_dispense=770.0,
    z_max=550.0,
    area=33.2,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.4,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_SBS_LiHa_Air_tip,
    ),
  )


def DiTi_1000ul_SBS_LiHa_Air(name: str) -> TecanTipRack:
  """DiTi 1000uL SBS tip rack for Air LiHa.

  Based on DiTi_1000ul_SBS_LiHa with AIRDITI tip type and corrected tip length.
  Z values TODO: teach from hardware.
  """
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=25.0,
    model="DiTi_1000ul_SBS_LiHa_Air",
    z_start=1180.0,   # 50uL z_start(770) + 41mm taller box (410 units)
    z_dispense=1180.0,
    z_max=960.0,      # 50uL z_max(550) + 410
    area=33.2,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_SBS_LiHa_Air_tip,
    ),
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
    z_start=770.0,  # taught: bare-channel tip top
    z_dispense=850.0,
    z_max=550.0,  # search down 30mm to ensure tips fully seat
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
