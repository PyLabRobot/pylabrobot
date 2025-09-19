from pylabrobot.resources.tip_rack import (
  NestedTipRack,
  TipRack,
  TipSpot,
)
from pylabrobot.resources.utils import create_ordered_items_2d

from .tip_creators import (
  fifty_ul_tip_no_filter,
  fifty_ul_tip_with_filter,
  five_ml_tip,
  four_ml_tip_with_filter,
  high_volume_tip_no_filter,
  high_volume_tip_with_filter,
  low_volume_tip_no_filter,
  low_volume_tip_with_filter,
  slim_standard_volume_tip_with_filter,
  standard_volume_tip_no_filter,
  standard_volume_tip_with_filter,
  ultrawide_high_volume_tip_with_filter,
  wide_high_volume_tip_with_filter,
)

# # # # # # # # # # 10 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_10ul_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235936 (CORE-II: conductive)
  Hamilton name: 'LTF'
  Tip Rack with 96x 10ul Low Volume Tip with filter
  """
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_10ul_filter",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


# TODO: identify cat number
def hamilton_96_tiprack_10ul(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: ?
  Hamilton name: 'LT'
  Tip Rack with 96x 10ul Low Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_10ul",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


# # # # # # # # # # 50 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_50ul_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235979 (CORE-II: conductive)
  Hamilton name: 'TIP_50ul'
  Tip Rack with 96x 50ul Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="Hamilton_96_tiprack_50ul_filter",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_50ul(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235978 (CORE-II: conductive)
  Hamilton name: 'TIP_50ul'
  Tip Rack with 96x 50ul Tip no filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="Hamilton_96_tiprack_50ul",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_50ul_NTR(name: str, with_tips: bool = True) -> NestedTipRack:
  """Hamilton cat. no.: 235983 (CORE-II:conductive), 235964 (CORE-II: clear)
  Nested Tip Rack with 96x 50ul Tips
  No filter
  """
  return NestedTipRack(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=56.0,  # Hamilton_96_tiprack_50ul_NTR + TIP_50ul_L.fitting_depth
    model="Hamilton_96_tiprack_50ul_NTR",
    stacking_z_height=16.0,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=9.45,
      dy=7.55,
      dz=56.0 - 40.5 - 2,
      # top of Hamilton_96_tiprack_50ul_NTR - TIP_50ul_L.max_tip_length - "inbetween-space"(?)
      item_dx=9.0,
      item_dy=9.0,
      size_x=8.15,
      size_y=8.15,
      make_tip=fifty_ul_tip_no_filter,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 3000 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_300ul_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235938 (CORE-II: conductive), 235903 (CORE-I: conductive)
  Hamilton name: 'STF'
  Tip Rack with 96x 300ul Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_300ul_filter",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_300ul(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235937 (CORE-II: conductive)
  Hamilton name: 'ST'
  Tip Rack with 96x 300ul Standard Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_300ul",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_300ul_filter_slim(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235646 (CORE-II: conductive)
  Hamilton name: 'STF_Slim'
  Tip Rack with 96x 300ul Slim Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_300ul_filter_slim",
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-83.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=slim_standard_volume_tip_with_filter,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 1_000 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_1000ul_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235940 (CORE-II: conductive), 235905 (CORE-I: conductive)
  Hamilton name: 'HTF'
  Tip Rack with 96x 1000ul High Volume Tip with filter
  """
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_1000ul_filter",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000ul(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235822 (CORE-II: clear tips)
  Hamilton name: 'HT'
  Tip Rack with 96x 1000ul High Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="Hamilton_96_tiprack_1000ul",
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000ul_filter_wide(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.:

  core-ii:
  - non-sterile, filter: 235678
  - sterile, filter: 235677
  core-i:
  - ?

  Hamilton name: 'HTF_WIDE'
  Tip Rack with 96x 1000ul High Volume Tip with filter

  Orifice Size: 1.2mm
  """

  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_1000ul_filter_wide.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-80.35,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=wide_high_volume_tip_with_filter,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000ul_filter_ultrawide(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.:

  core-ii:
  - non-sterile, filter: 235541
  - sterile, filter: 235842
  core-i:
  - ?

  Hamilton name: 'HTF_ULTRAWIDE'
  Tip Rack with 96x 1000ul High Volume Tip with filter

  Orifice Size: 3.2mm
  """

  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_1000ul_filter_ultrawide.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-68.4,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=ultrawide_high_volume_tip_with_filter,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 4 ml Tips # # # # # # # # # #


def hamilton_24_tiprack_4000ul_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 184021 (non-sterile), 184023 (sterile)
  Hamilton name: 'FourmlTF'
  Tip Rack 24x 4ml Tip with Filter landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model=hamilton_24_tiprack_4000ul_filter.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


# # # # # # # # # # 5 ml Tips # # # # # # # # # #


def hamilton_24_tiprack_5000ul(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 184020 (non-sterile), 184022 (sterile)
  Hamilton name: 'FivemlT'
  Tip Rack 24x 5ml Tip landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model=hamilton_24_tiprack_5000ul.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
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
    with_tips=with_tips,
  )


# # # # # # # # # # Deprecation Warnings # # # # # # # # # #

# TODO: remove after December 2025 (giving approx. 3 month transition period)


def LTF(name: str) -> TipRack:
  raise NotImplementedError("LTF is deprecated. use hamilton_96_tiprack_10ul_filter instead")


def LT(name: str) -> TipRack:
  raise NotImplementedError("LT is deprecated. use hamilton_96_tiprack_10ul instead")


def TIP_50ul_w_filter(name: str) -> TipRack:
  raise NotImplementedError(
    "TIP_50ul_w_filter is deprecated. use hamilton_96_tiprack_50ul_filter instead"
  )


def TIP_50ul(name: str) -> TipRack:
  raise NotImplementedError("TIP_50ul is deprecated. use hamilton_96_tiprack_50ul instead")


def STF(name: str) -> TipRack:
  raise NotImplementedError("STF is deprecated. use hamilton_96_tiprack_300ul_filter instead")


def ST(name: str) -> TipRack:
  raise NotImplementedError("ST is deprecated. use hamilton_96_tiprack_300ul instead")


def STF_Slim(name: str) -> TipRack:
  raise NotImplementedError(
    "STF_Slim is deprecated. use hamilton_96_tiprack_300ul_filter_slim instead"
  )


def HTF(name: str) -> TipRack:
  raise NotImplementedError("HTF is deprecated. use hamilton_96_tiprack_1000ul_filter instead")


def HT(name: str) -> TipRack:
  raise NotImplementedError("HT is deprecated. use hamilton_96_tiprack_1000ul instead")
