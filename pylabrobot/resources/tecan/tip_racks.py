""" Tecan tip racks """

# pylint: disable=empty-docstring
# pylint: disable=invalid-name
# pylint: disable=line-too-long

from typing import List, Optional
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.tecan.tecan_resource import TecanResource
from .tip_creators import (
  DiTi_100ul_Te_MO_tip,
  DiTi_50ul_Te_MO_tip,
  DiTi_200ul_Te_MO_tip,
  DiTi_100ul_Filter_Te_MO_tip,
  DiTi_200ul_Filter_Te_MO_tip,
  Adapter_96_DiTi_MCA384_tip,
  Adapter_DiTi_Combo_MCA384_tip,
  Adapter_DiTi_MCA384_tip,
  DiTi_100ul_Filter_MCA96_tip,
  DiTi_100ul_MCA96_tip,
  DiTi_200ul_Filter_MCA96_tip,
  DiTi_200ul_MCA96_tip,
  DiTi_50ul_MCA96_tip,
  Base_Nested_DiTi_MCA96_tip,
  DiTi_100ul_Nested_MCA96_tip,
  DiTi_100ul_SBS_MCA96_tip,
  DiTi_200ul_SBS_MCA96_tip,
  DiTi_50ul_SBS_MCA96_tip,
  DiTi_50ul_Nested_MCA96_tip,
  Adapter_96_DiTi_1to1_MCA384_tip,
  DiTi_200ul_Nested_MCA96_tip,
  DiTi_200ul_w_b_filter_MCA96_tip,
  DiTi_200ul_wide_bore_MCA96_tip,
  Adapter_96_DiTi_4to1_MCA384_tip,
  DiTi_500ul_Filter_SBS_MCA96_tip,
  DiTi_500ul_SBS_MCA96_tip,
  DiTi_Nested_Waste_MCA384_tip,
  DiTi_1000ul_SBS_LiHa_tip,
  DiTi_200ul_SBS_LiHa_tip,
  DiTi_50ul_SBS_LiHa_tip,
  DiTi_5000ul_LiHa_tip,
  DiTi_5000ul_Filter_LiHa_tip,
  DiTi_125ul_Filter_MCA384_tip,
  DiTi_125ul_MCA384_tip,
  DiTi_15ul_Filter_MCA384_tip,
  DiTi_15ul_MCA384_tip,
  DiTi_50ul_Filter_MCA384_tip,
  DiTi_50ul_MCA384_tip,
  DiTi_1000ul_Filter_LiHa_tip,
  DiTi_1000ul_LiHa_tip,
  DiTi_10ul_Filter_LiHa_tip,
  DiTi_10ul_LiHa_tip,
  DiTi_200ul_Filter_LiHa_tip,
  DiTi_200ul_LiHa_tip,
  DiTi_50ul_Filter_LiHa_tip,
  DiTi_50ul_LiHa_tip,
  DiTi_350ul_Nested_LiHa_tip,
  DiTi_10ul_Filter_LiHa_L_tip,
  DiTi_10ul_Filter_Nested_LiHa_tip,
  DiTi_10ul_LiHa_L_tip,
  DiTi_10ul_Nested_LiHa_tip,
  DiTi_10ul_SBS_Filter_LiHa_tip,
  DiTi_10ul_SBS_LiHa_tip,
  DiTi_1000ul_W_B_Filter_LiHa_tip,
  DiTi_1000ul_CL_Filter_LiHa_tip,
  DiTi_1000ul_CL_LiHa_tip,
  DiTi_200ul_CL_Filter_LiHa_tip,
  DiTi_200ul_CL_LiHa_tip,
  DiTi_50ul_CL_Filter_LiHa_tip,
  DiTi_50ul_CL_LiHa_tip,
)


class TecanTipRack(TipRack, TecanResource):
  """ Base class for Tecan tip racks. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    z_travel: float,
    z_start: float,
    z_dispense: float,
    z_max: float,
    area: float,
    items: Optional[List[List[TipSpot]]] = None,
    category: str = "tecan_plate",
    model: Optional[str] = None
  ):
    super().__init__(name, size_x, size_y, size_z, items, category=category, model=model)

    self.z_travel = z_travel
    self.z_start = z_start
    self.z_dispense = z_dispense
    self.z_max = z_max
    self.area = area



def DiTi_100ul_Te_MO(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=15.0,
    model="DiTi_100ul_Te_MO",
    z_travel=1230.0,
    z_start=1230.0,
    z_dispense=1280.0,
    z_max=1430.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=42.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_Te_MO_tip
    ),
  )


def DiTi_50ul_Te_MO(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=14.0,
    model="DiTi_50ul_Te_MO",
    z_travel=1230.0,
    z_start=1230.0,
    z_dispense=1290.0,
    z_max=1430.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=28.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_Te_MO_tip
    ),
  )


def DiTi_200ul_Te_MO(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=14.0,
    model="DiTi_200ul_Te_MO",
    z_travel=1230.0,
    z_start=1230.0,
    z_dispense=1290.0,
    z_max=1430.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=42.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_Te_MO_tip
    ),
  )


def DiTi_100ul_Filter_Te_MO(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=6.7,
    model="DiTi_100ul_Filter_Te_MO",
    z_travel=1230.0,
    z_start=1230.0,
    z_dispense=1290.0,
    z_max=1357.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=42.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_Filter_Te_MO_tip
    ),
  )


def DiTi_200ul_Filter_Te_MO(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=14.0,
    model="DiTi_200ul_Filter_Te_MO",
    z_travel=1230.0,
    z_start=1230.0,
    z_dispense=1290.0,
    z_max=1430.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=42.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_Filter_Te_MO_tip
    ),
  )


def Adapter_96_DiTi_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30032043 or 30032063 - Picks 96 tips, a single row or a single column. Works with MCA384 disposable tips. """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=80.4,
    size_z=3.9,
    model="Adapter_96_DiTi_MCA384",
    z_travel=1284.0,
    z_start=1422.0,
    z_dispense=1422.0,
    z_max=1461.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=4.2,
      dz=15.9,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=Adapter_96_DiTi_MCA384_tip
    ),
  )


def Adapter_DiTi_Combo_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30032060 - Picks 384 tips, a single row of 24 tips, a single column of 16 tips or two columns of 16 tips each. """
  return TecanTipRack(
    name=name,
    size_x=128.4,
    size_y=85.4,
    size_z=3.5,
    model="Adapter_DiTi_Combo_MCA384",
    z_travel=1284.0,
    z_start=1422.0,
    z_dispense=1422.0,
    z_max=1457.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=6.45,
      dz=15.9,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=Adapter_DiTi_Combo_MCA384_tip
    ),
  )


def Adapter_DiTi_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30032061 - Picks 384 tips. Works with MCA384 disposable tips (Former Part no. 30032041). """
  return TecanTipRack(
    name=name,
    size_x=128.4,
    size_y=85.4,
    size_z=3.9,
    model="Adapter_DiTi_MCA384",
    z_travel=1284.0,
    z_start=1422.0,
    z_dispense=1422.0,
    z_max=1461.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=6.45,
      dz=15.9,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=Adapter_DiTi_MCA384_tip
    ),
  )


def DiTi_100ul_Filter_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 10612347 """
  return TecanTipRack(
    name=name,
    size_x=120.6,
    size_y=84.2,
    size_z=20.4,
    model="DiTi_100ul_Filter_MCA96",
    z_travel=1380.0,
    z_start=1541.0,
    z_dispense=1531.0,
    z_max=1735.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=6.3,
      dy=5.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_Filter_MCA96_tip
    ),
  )


def DiTi_100ul_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 10612345 or 10612346 """
  return TecanTipRack(
    name=name,
    size_x=120.6,
    size_y=84.2,
    size_z=20.4,
    model="DiTi_100ul_MCA96",
    z_travel=1380.0,
    z_start=1541.0,
    z_dispense=1531.0,
    z_max=1735.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=6.3,
      dy=5.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_MCA96_tip
    ),
  )


def DiTi_200ul_Filter_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 10612342 """
  return TecanTipRack(
    name=name,
    size_x=120.6,
    size_y=84.2,
    size_z=20.4,
    model="DiTi_200ul_Filter_MCA96",
    z_travel=1380.0,
    z_start=1541.0,
    z_dispense=1531.0,
    z_max=1735.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=6.3,
      dy=5.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_Filter_MCA96_tip
    ),
  )


def DiTi_200ul_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 10612340 or 10612341 """
  return TecanTipRack(
    name=name,
    size_x=120.6,
    size_y=84.2,
    size_z=20.4,
    model="DiTi_200ul_MCA96",
    z_travel=1380.0,
    z_start=1541.0,
    z_dispense=1531.0,
    z_max=1735.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=6.3,
      dy=5.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_MCA96_tip
    ),
  )


def DiTi_50ul_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 10612343 """
  return TecanTipRack(
    name=name,
    size_x=120.6,
    size_y=84.2,
    size_z=20.4,
    model="DiTi_50ul_MCA96",
    z_travel=1380.0,
    z_start=1541.0,
    z_dispense=1531.0,
    z_max=1735.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=6.3,
      dy=5.8,
      dz=29.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_MCA96_tip
    ),
  )


def Base_Nested_DiTi_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30038609 or 30038614 or 30038619 """
  return TecanTipRack(
    name=name,
    size_x=124.8,
    size_y=89.2,
    size_z=-0.2,
    model="Base_Nested_DiTi_MCA96",
    z_travel=3243.0,
    z_start=3282.0,
    z_dispense=3282.0,
    z_max=3280.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=4,
      num_items_y=4,
      dx=-3.6,
      dy=-3.7,
      dz=43.1,
      item_dx=33.0,
      item_dy=33.0,
      size_x=33.0,
      size_y=33.0,
      make_tip=Base_Nested_DiTi_MCA96_tip
    ),
  )


def DiTi_100ul_Nested_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30038614 """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=85.0,
    size_z=16.6,
    model="DiTi_100ul_Nested_MCA96",
    z_travel=1933.0,
    z_start=1954.0,
    z_dispense=1933.0,
    z_max=2099.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=6.5,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_Nested_MCA96_tip
    ),
  )


def DiTi_100ul_SBS_MCA96(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=88.2,
    size_z=26.0,
    model="DiTi_100ul_SBS_MCA96",
    z_travel=1113.0,
    z_start=1538.0,
    z_dispense=1478.0,
    z_max=1738.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_100ul_SBS_MCA96_tip
    ),
  )


def DiTi_200ul_SBS_MCA96(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=88.2,
    size_z=26.0,
    model="DiTi_200ul_SBS_MCA96",
    z_travel=1113.0,
    z_start=1538.0,
    z_dispense=1478.0,
    z_max=1738.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_SBS_MCA96_tip
    ),
  )


def DiTi_50ul_SBS_MCA96(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=88.2,
    size_z=25.0,
    model="DiTi_50ul_SBS_MCA96",
    z_travel=1113.0,
    z_start=1538.0,
    z_dispense=1478.0,
    z_max=1728.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.8,
      dz=29.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_SBS_MCA96_tip
    ),
  )


def DiTi_50ul_Nested_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30038609 """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=85.0,
    size_z=16.6,
    model="DiTi_50ul_Nested_MCA96",
    z_travel=1933.0,
    z_start=1954.0,
    z_dispense=1933.0,
    z_max=2099.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=6.5,
      dz=29.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_Nested_MCA96_tip
    ),
  )


def Adapter_96_DiTi_1to1_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30032048 - Picks 96 tips, single rows/columns. Uses 1channel per tip. Works with MCA96 disposable tips. """
  return TecanTipRack(
    name=name,
    size_x=128.4,
    size_y=85.4,
    size_z=3.9,
    model="Adapter_96_DiTi_1to1_MCA384",
    z_travel=1284.0,
    z_start=1422.0,
    z_dispense=1422.0,
    z_max=1461.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.45,
      dy=3.95,
      dz=25.2,
      item_dx=9.5,
      item_dy=9.5,
      size_x=9.5,
      size_y=9.5,
      make_tip=Adapter_96_DiTi_1to1_MCA384_tip
    ),
  )


def DiTi_200ul_Nested_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30038619 """
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=84.8,
    size_z=16.6,
    model="DiTi_200ul_Nested_MCA96",
    z_travel=1933.0,
    z_start=1954.0,
    z_dispense=1933.0,
    z_max=2099.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.4,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_Nested_MCA96_tip
    ),
  )


def DiTi_200ul_w_b_filter_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30050349, not for volumes under 5ul """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=88.2,
    size_z=26.0,
    model="DiTi_200ul_w_b_filter_MCA96",
    z_travel=1113.0,
    z_start=1538.0,
    z_dispense=1478.0,
    z_max=1738.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_w_b_filter_MCA96_tip
    ),
  )


def DiTi_200ul_wide_bore_MCA96(name: str) -> TecanTipRack:
  """ Tecan part no. 30050348, not for volumes under 5ul """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=88.2,
    size_z=26.0,
    model="DiTi_200ul_wide_bore_MCA96",
    z_travel=1113.0,
    z_start=1538.0,
    z_dispense=1478.0,
    z_max=1738.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.8,
      dz=43.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_wide_bore_MCA96_tip
    ),
  )


def Adapter_96_DiTi_4to1_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30032042 or 30032062 - EVA (Extended Volume Adapter) - Maximum capacity 500 uL. Use with SBS 96 tip box only. """
  return TecanTipRack(
    name=name,
    size_x=129.2,
    size_y=85.4,
    size_z=3.9,
    model="Adapter_96_DiTi_4to1_MCA384",
    z_travel=1284.0,
    z_start=1422.0,
    z_dispense=1422.0,
    z_max=1461.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.85,
      dy=3.95,
      dz=25.2,
      item_dx=9.5,
      item_dy=9.5,
      size_x=9.5,
      size_y=9.5,
      make_tip=Adapter_96_DiTi_4to1_MCA384_tip
    ),
  )


def DiTi_500ul_Filter_SBS_MCA96(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=129.6,
    size_y=86.6,
    size_z=15.0,
    model="DiTi_500ul_Filter_SBS_MCA96",
    z_travel=1128.0,
    z_start=1453.0,
    z_dispense=1410.0,
    z_max=1560.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.8,
      dy=6.9,
      dz=54.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_500ul_Filter_SBS_MCA96_tip
    ),
  )


def DiTi_500ul_SBS_MCA96(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=129.2,
    size_y=86.8,
    size_z=14.0,
    model="DiTi_500ul_SBS_MCA96",
    z_travel=1156.0,
    z_start=1438.0,
    z_dispense=1438.0,
    z_max=1578.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.6,
      dy=6.9,
      dz=54.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_500ul_SBS_MCA96_tip
    ),
  )


def DiTi_Nested_Waste_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30054549 """
  return TecanTipRack(
    name=name,
    size_x=166.0,
    size_y=95.0,
    size_z=0.0,
    model="DiTi_Nested_Waste_MCA384",
    z_travel=1917.0,
    z_start=1940.0,
    z_dispense=1940.0,
    z_max=1940.0,
    area=20385.0,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=29.0,
      dy=11.5,
      dz=0.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_Nested_Waste_MCA384_tip
    ),
  )


def DiTi_1000ul_SBS_LiHa(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.0,
    size_y=85.8,
    size_z=25.0,
    model="DiTi_1000ul_SBS_LiHa",
    z_travel=760.0,
    z_start=1010.0,
    z_dispense=1010.0,
    z_max=1260.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.0,
      dy=6.9,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_SBS_LiHa_tip
    ),
  )


def DiTi_200ul_SBS_LiHa(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=86.8,
    size_z=30.0,
    model="DiTi_200ul_SBS_LiHa",
    z_travel=1010.0,
    z_start=1360.0,
    z_dispense=1360.0,
    z_max=1660.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.4,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_SBS_LiHa_tip
    ),
  )


def DiTi_50ul_SBS_LiHa(name: str) -> TecanTipRack:
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=86.0,
    size_z=30.0,
    model="DiTi_50ul_SBS_LiHa",
    z_travel=1010.0,
    z_start=1360.0,
    z_dispense=1360.0,
    z_max=1660.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.0,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_SBS_LiHa_tip
    ),
  )


def DiTi_5000ul_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30059897 (Tecan Pure). Tip is usable in a volume range of 300ul to 4850ul. """
  return TecanTipRack(
    name=name,
    size_x=129.0,
    size_y=87.4,
    size_z=30.0,
    model="DiTi_5000ul_LiHa",
    z_travel=710.0,
    z_start=850.0,
    z_dispense=850.0,
    z_max=1150.0,
    area=50.0,
    items=create_equally_spaced(TipSpot,
      num_items_x=6,
      num_items_y=4,
      dx=10.5,
      dy=7.6,
      dz=46.6,
      item_dx=18.0,
      item_dy=18.0,
      size_x=18.0,
      size_y=18.0,
      make_tip=DiTi_5000ul_LiHa_tip
    ),
  )


def DiTi_5000ul_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30065423 (Tecan Pure), Tip is usable in a volume range of 300ul to 4800ul. """
  return TecanTipRack(
    name=name,
    size_x=129.0,
    size_y=87.4,
    size_z=30.0,
    model="DiTi_5000ul_Filter_LiHa",
    z_travel=710.0,
    z_start=850.0,
    z_dispense=850.0,
    z_max=1150.0,
    area=50.0,
    items=create_equally_spaced(TipSpot,
      num_items_x=6,
      num_items_y=4,
      dx=10.5,
      dy=7.6,
      dz=46.6,
      item_dx=18.0,
      item_dy=18.0,
      size_x=18.0,
      size_y=18.0,
      make_tip=DiTi_5000ul_Filter_LiHa_tip
    ),
  )


def DiTi_125ul_Filter_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051810 (Tecan Sterile). Maximum pipetting volume is 110ul. """
  return TecanTipRack(
    name=name,
    size_x=127.3,
    size_y=85.3,
    size_z=20.0,
    model="DiTi_125ul_Filter_MCA384",
    z_travel=1400.0,
    z_start=1520.0,
    z_dispense=1490.0,
    z_max=1690.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.65,
      dy=6.65,
      dz=47.3,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_125ul_Filter_MCA384_tip
    ),
  )


def DiTi_125ul_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051808 (Tecan Pure) or 30051809 (Tecan Sterile) """
  return TecanTipRack(
    name=name,
    size_x=127.7,
    size_y=84.9,
    size_z=20.0,
    model="DiTi_125ul_MCA384",
    z_travel=1400.0,
    z_start=1520.0,
    z_dispense=1490.0,
    z_max=1690.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.85,
      dy=6.45,
      dz=47.3,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_125ul_MCA384_tip
    ),
  )


def DiTi_15ul_Filter_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051804 (Tecan Sterile). Maximum pipetting volume is 14.5ul. """
  return TecanTipRack(
    name=name,
    size_x=127.9,
    size_y=84.9,
    size_z=20.3,
    model="DiTi_15ul_Filter_MCA384",
    z_travel=1592.0,
    z_start=1705.0,
    z_dispense=1676.0,
    z_max=1879.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=6.45,
      dz=28.6,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_15ul_Filter_MCA384_tip
    ),
  )


def DiTi_15ul_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051802 (Tecan Pure) or 30051803 (Tecan Sterile) """
  return TecanTipRack(
    name=name,
    size_x=127.9,
    size_y=84.9,
    size_z=20.3,
    model="DiTi_15ul_MCA384",
    z_travel=1592.0,
    z_start=1705.0,
    z_dispense=1676.0,
    z_max=1879.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.95,
      dy=6.45,
      dz=28.6,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_15ul_MCA384_tip
    ),
  )


def DiTi_50ul_Filter_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051807 (Tecan Sterile). Maximum pipetting volume is 40ul. """
  return TecanTipRack(
    name=name,
    size_x=127.3,
    size_y=85.3,
    size_z=20.0,
    model="DiTi_50ul_Filter_MCA384",
    z_travel=1400.0,
    z_start=1520.0,
    z_dispense=1490.0,
    z_max=1690.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.65,
      dy=6.65,
      dz=41.4,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_50ul_Filter_MCA384_tip
    ),
  )


def DiTi_50ul_MCA384(name: str) -> TecanTipRack:
  """ Tecan part no. 30051805 (Tecan Pure) or 30051806 (Tecan Sterile) """
  return TecanTipRack(
    name=name,
    size_x=127.3,
    size_y=85.3,
    size_z=20.0,
    model="DiTi_50ul_MCA384",
    z_travel=1400.0,
    z_start=1520.0,
    z_dispense=1490.0,
    z_max=1690.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=24,
      num_items_y=16,
      dx=9.65,
      dy=6.65,
      dz=41.4,
      item_dx=4.5,
      item_dy=4.5,
      size_x=4.5,
      size_y=4.5,
      make_tip=DiTi_50ul_MCA384_tip
    ),
  )


def DiTi_1000ul_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612513 or 10612555 or 30000631 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_1000ul_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_Filter_LiHa_tip
    ),
  )


def DiTi_1000ul_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612554 or 30000630 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_1000ul_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_LiHa_tip
    ),
  )


def DiTi_10ul_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612517 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_10ul_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-31.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_Filter_LiHa_tip
    ),
  )


def DiTi_10ul_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612516 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_10ul_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-31.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_LiHa_tip
    ),
  )


def DiTi_200ul_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612511 or 10612553 or 30000629 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_200ul_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_Filter_LiHa_tip
    ),
  )


def DiTi_200ul_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 10612552 or 30000627 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_200ul_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_LiHa_tip
    ),
  )


def DiTi_50ul_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30032114 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_50ul_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_Filter_LiHa_tip
    ),
  )


def DiTi_50ul_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30032115 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_50ul_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_LiHa_tip
    ),
  )


def DiTi_350ul_Nested_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30083400(Tecan Pure), 30083401 (Tecan Sterile) """
  return TecanTipRack(
    name=name,
    size_x=130.4,
    size_y=86.2,
    size_z=16.0,
    model="DiTi_350ul_Nested_LiHa",
    z_travel=1815.0,
    z_start=2015.0,
    z_dispense=2015.0,
    z_max=2175.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=11.2,
      dy=7.1,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_350ul_Nested_LiHa_tip
    ),
  )


def DiTi_10ul_Filter_LiHa_L(name: str) -> TecanTipRack:
  """ Tecan part no. 30104804 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_10ul_Filter_LiHa_L",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_Filter_LiHa_L_tip
    ),
  )


def DiTi_10ul_Filter_Nested_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30104978 (Tecan Pure), 30104979 (Tecan Sterile) """
  return TecanTipRack(
    name=name,
    size_x=127.6,
    size_y=86.0,
    size_z=16.0,
    model="DiTi_10ul_Filter_Nested_LiHa",
    z_travel=1815.0,
    z_start=2015.0,
    z_dispense=2015.0,
    z_max=2175.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=9.8,
      dy=7.0,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_Filter_Nested_LiHa_tip
    ),
  )


def DiTi_10ul_LiHa_L(name: str) -> TecanTipRack:
  """ Tecan part no. 30104803 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_10ul_LiHa_L",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_LiHa_L_tip
    ),
  )


def DiTi_10ul_Nested_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30104977 (Tecan Pure) """
  return TecanTipRack(
    name=name,
    size_x=127.6,
    size_y=87.4,
    size_z=16.0,
    model="DiTi_10ul_Nested_LiHa",
    z_travel=1815.0,
    z_start=2015.0,
    z_dispense=2015.0,
    z_max=2175.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=9.8,
      dy=7.7,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_Nested_LiHa_tip
    ),
  )


def DiTi_10ul_SBS_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30104976 (Tecan Sterile), 30104974 (Tecan Pure, Refill) """
  return TecanTipRack(
    name=name,
    size_x=128.2,
    size_y=86.8,
    size_z=30.0,
    model="DiTi_10ul_SBS_Filter_LiHa",
    z_travel=1010.0,
    z_start=1360.0,
    z_dispense=1360.0,
    z_max=1660.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.1,
      dy=7.4,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_SBS_Filter_LiHa_tip
    ),
  )


def DiTi_10ul_SBS_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30104975 (Tecan Sterile), 30104973 (Tecan Pure, Refill) """
  return TecanTipRack(
    name=name,
    size_x=129.0,
    size_y=83.6,
    size_z=30.0,
    model="DiTi_10ul_SBS_LiHa",
    z_travel=1010.0,
    z_start=1360.0,
    z_dispense=1360.0,
    z_max=1660.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.5,
      dy=5.8,
      dz=-28.1,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_10ul_SBS_LiHa_tip
    ),
  )


def DiTi_1000ul_W_B_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30115239 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=91.2,
    size_z=22.2,
    model="DiTi_1000ul_W_B_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=9.6,
      dz=32.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_W_B_Filter_LiHa_tip
    ),
  )


def DiTi_1000ul_CL_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126020 or 30126095 """
  return TecanTipRack(
    name=name,
    size_x=127.6,
    size_y=88.2,
    size_z=22.2,
    model="DiTi_1000ul_CL_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=9.8,
      dy=8.1,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_CL_Filter_LiHa_tip
    ),
  )


def DiTi_1000ul_CL_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126019 or 30126094 """
  return TecanTipRack(
    name=name,
    size_x=127.6,
    size_y=88.2,
    size_z=22.2,
    model="DiTi_1000ul_CL_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=9.8,
      dy=8.1,
      dz=32.6,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_1000ul_CL_LiHa_tip
    ),
  )


def DiTi_200ul_CL_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126018 or 30126093 """
  return TecanTipRack(
    name=name,
    size_x=129.4,
    size_y=87.4,
    size_z=22.2,
    model="DiTi_200ul_CL_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.7,
      dy=7.7,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_CL_Filter_LiHa_tip
    ),
  )


def DiTi_200ul_CL_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126017 or 30126092 """
  return TecanTipRack(
    name=name,
    size_x=129.4,
    size_y=87.4,
    size_z=22.2,
    model="DiTi_200ul_CL_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=10.7,
      dy=7.7,
      dz=-5.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_200ul_CL_LiHa_tip
    ),
  )


def DiTi_50ul_CL_Filter_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126097 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_50ul_CL_Filter_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_CL_Filter_LiHa_tip
    ),
  )


def DiTi_50ul_CL_LiHa(name: str) -> TecanTipRack:
  """ Tecan part no. 30126096 """
  return TecanTipRack(
    name=name,
    size_x=123.4,
    size_y=89.4,
    size_z=22.2,
    model="DiTi_50ul_CL_LiHa",
    z_travel=807.0,
    z_start=877.0,
    z_dispense=865.0,
    z_max=1087.0,
    area=33.2,
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.7,
      dy=8.7,
      dz=-5.3,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=DiTi_50ul_CL_LiHa_tip
    ),
  )
