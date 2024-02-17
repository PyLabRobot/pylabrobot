""" ML Star tips """

# pylint: skip-file

from pylabrobot.resources.itemized_resource import create_equally_spaced
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from .tip_creators import (
  low_volume_tip_no_filter,
  low_volume_tip_with_filter,
  standard_volume_tip_no_filter,
  standard_volume_tip_with_filter,
  high_volume_tip_no_filter,
  high_volume_tip_with_filter,
  four_ml_tip_with_filter,
  five_ml_tip,
  fifty_ul_tip_with_filter,
  fifty_ul_tip_no_filter
)


#: Tip Rack 24x 4ml Tip with Filter landscape oriented
def FourmlTF_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model="FourmlTF_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=6,
      num_items_y=4,
      dx=7.3,
      dy=5.2,
      dz=-93.2,
      item_dx=18.0,
      item_dy=18.0,
      size_x=18.0,
      size_y=18.0,
      make_tip=four_ml_tip_with_filter,
    ),
    with_tips=with_tips
  )


#: Tip Rack 24x 4ml Tip with Filter portrait oriented
def FourmlTF_P(name: str, with_tips: bool = True) -> TipRack:
  return FourmlTF_L(name=name, with_tips=with_tips).rotated(90)


#: Tip Rack 24x 5ml Tip landscape oriented
def FivemlT_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model="FivemlT_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=6,
      num_items_y=4,
      dx=7.3,
      dy=5.2,
      dz=-93.2,
      item_dx=18.0,
      item_dy=18.0,
      size_x=18.0,
      size_y=18.0,
      make_tip=five_ml_tip,
    ),
    with_tips=with_tips
  )


#: Tip Rack 24x 5ml Tip portrait oriented
def FivemlT_P(name: str, with_tips: bool = True) -> TipRack:
  return FivemlT_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 1000ul High Volume Tip with filter
def HTF_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="HTF_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-83.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=high_volume_tip_with_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 1000ul High Volume Tip with filter (portrait)
def HTF_P(name: str, with_tips: bool = True) -> TipRack:
  return HTF_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 1000ul High Volume Tip
def HT_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="HT_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-83.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=high_volume_tip_no_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 1000ul High Volume Tip (portrait)
def HT_P(name: str, with_tips: bool = True) -> TipRack:
  return HT_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 10ul Low Volume Tip with filter
def LTF_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="LTF_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-22.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=low_volume_tip_with_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 10ul Low Volume Tip with filter (portrait)
def LTF_P(name: str, with_tips: bool = True) -> TipRack:
  return LTF_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 10ul Low Volume Tip
def LT_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="LT_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-22.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=low_volume_tip_no_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 10ul Low Volume Tip (portrait)
def LT_P(name: str, with_tips: bool = True) -> TipRack:
  return LT_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 300ul Standard Volume Tip with filter
def STF_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="STF_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-50.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=standard_volume_tip_with_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 300ul Standard Volume Tip with filter (portrait)
def STF_P(name: str, with_tips: bool = True) -> TipRack:
  return STF_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 300ul Standard Volume Tip
def ST_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="ST_L",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-50.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=standard_volume_tip_no_filter,
    ),
    with_tips=with_tips
  )


#: Rack with 96 300ul Standard Volume Tip (portrait)
def ST_P(name: str, with_tips: bool = True) -> TipRack:
  return ST_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 50ul Tip with filter
def TIP_50ul_w_filter_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="TIP_50ul_w_filter",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-40.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=fifty_ul_tip_with_filter,
    ),
    with_tips=with_tips
  )


#: Tip Rack 96 50ul Tip with filter portrait oriented
def TIP_50ul_w_filter_P(name: str, with_tips: bool = True) -> TipRack:
  return TIP_50ul_w_filter_L(name=name, with_tips=with_tips).rotated(90)


#: Rack with 96 50ul Tip
def TIP_50ul_L(name: str, with_tips: bool = True) -> TipRack:
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="TIP_50ul",
    items=create_equally_spaced(TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-40.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=fifty_ul_tip_no_filter,
    ),
    with_tips=with_tips
  )


#: Tip Rack 96 50ul Tip portrait oriented
def TIP_50ul_P(name: str, with_tips: bool = True) -> TipRack:
  return TIP_50ul_L(name=name, with_tips=with_tips).rotated(90)
