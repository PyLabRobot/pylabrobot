from pylabrobot.resources.tip_rack import (
  NestedTipRack,
  TipRack,
  TipSpot,
)
from pylabrobot.resources.utils import create_ordered_items_2d

from .tip_creators import (
  hamilton_tip_10uL,
  hamilton_tip_10uL_filter,
  hamilton_tip_50uL,
  hamilton_tip_50uL_filter,
  hamilton_tip_300uL,
  hamilton_tip_300uL_filter,
  hamilton_tip_300uL_filter_slim,
  hamilton_tip_300uL_filter_ultrawide,
  hamilton_tip_1000uL,
  hamilton_tip_1000uL_filter,
  hamilton_tip_1000uL_filter_ultrawide,
  hamilton_tip_1000uL_filter_wide,
  hamilton_tip_4000uL_filter,
  hamilton_tip_5000uL,
)

# # # # # # # # # # 10 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_10uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235936 (sterile), 235901 (non-sterile)
  Hamilton name: 'LTF'
  Tip Rack with 96x 10ul Low Volume Tip with filter
  """
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_10uL_filter.__name__,
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
      make_tip=hamilton_tip_10uL_filter,
    ),
    with_tips=with_tips,
  )


# TODO: identify cat number
def hamilton_96_tiprack_10uL(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235900 (non-sterile) 235935 (sterile)
  Hamilton name: 'LT'
  Tip Rack with 96x 10ul Low Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_10uL.__name__,
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
      make_tip=hamilton_tip_10uL,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 50 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_50uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235948 (non-sterile), 235979 (sterile), 235829 (clear, non-sterile)
  Hamilton name: 'TIP_50ul_w_filter'
  Tip Rack with 96x 50ul Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model=hamilton_96_tiprack_50uL_filter.__name__,
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
      make_tip=hamilton_tip_50uL_filter,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_50uL(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235966 (non-sterile) 235978 (sterile)
  Hamilton name: 'TIP_50ul'
  Tip Rack with 96x 50ul Tip no filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model=hamilton_96_tiprack_50uL.__name__,
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
      make_tip=hamilton_tip_50uL,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_50uL_NTR(name: str, with_tips: bool = True) -> NestedTipRack:
  """Hamilton cat. no.: 235947 (non-sterile), 235964 (clear, non-sterile), 235987 (sterile)
  Nested Tip Rack with 96x 50ul Tips
  No filter
  """
  return NestedTipRack(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=56.0,  # Hamilton_96_tiprack_50ul_NTR + TIP_50ul_L.fitting_depth
    model=hamilton_96_tiprack_50uL_NTR.__name__,
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
      make_tip=hamilton_tip_50uL,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 300 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_300uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235830 (clear, non-sterile), 235903 (non-sterile), 235938 (sterile)
  Hamilton name: 'STF'
  Tip Rack with 96x 300ul Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_300uL_filter.__name__,
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
      make_tip=hamilton_tip_300uL_filter,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_300uL(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235834 (clear, non-sterile), 235902 (non-sterile), 235937 (sterile)
  Hamilton name: 'ST'
  Tip Rack with 96x 300ul Standard Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_300uL.__name__,
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
      make_tip=hamilton_tip_300uL,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_300uL_filter_slim(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235646 (CORE-II: conductive)
  Hamilton name: 'STF_Slim'
  Tip Rack with 96x 300ul Slim Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_300uL_filter_slim.__name__,
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
      make_tip=hamilton_tip_300uL_filter_slim,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_300uL_filter_ultrawide(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235449 (1.55 mm oriface, non-sterile)
  Hamilton name: 'STF'
  Tip Rack with 96x 300ul Wide Bore Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_300uL_filter_ultrawide.__name__,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      dx=7.2,
      dy=5.3,
      dz=-42.5,
      item_dx=9.0,
      item_dy=9.0,
      size_x=9.0,
      size_y=9.0,
      make_tip=hamilton_tip_300uL_filter_ultrawide,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 1_000 uL Tips # # # # # # # # # #


def hamilton_96_tiprack_1000uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235820 (clear, non-sterile), 235905 (non-sterile), 235940 (sterile)
  Hamilton name: 'HTF'
  Tip Rack with 96x 1000ul High Volume Tip with filter
  """
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_1000uL_filter.__name__,
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
      make_tip=hamilton_tip_1000uL_filter,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000uL(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 235822 (clear, non-sterile), 235904 (non-sterile), 235939 (sterile)
  Hamilton name: 'HT'
  Tip Rack with 96x 1000ul High Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_96_tiprack_1000uL.__name__,
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
      make_tip=hamilton_tip_1000uL,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000uL_filter_wide(name: str, with_tips: bool = True) -> TipRack:
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
    model=hamilton_96_tiprack_1000uL_filter_wide.__name__,
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
      make_tip=hamilton_tip_1000uL_filter_wide,
    ),
    with_tips=with_tips,
  )


def hamilton_96_tiprack_1000uL_filter_ultrawide(name: str, with_tips: bool = True) -> TipRack:
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
    model=hamilton_96_tiprack_1000uL_filter_ultrawide.__name__,
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
      make_tip=hamilton_tip_1000uL_filter_ultrawide,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 4 ml Tips # # # # # # # # # #


def hamilton_24_tiprack_4000uL_filter(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 184021 (non-sterile), 184023 (sterile)
  Hamilton name: 'FourmlTF'
  Tip Rack 24x 4ml Tip with Filter landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model=hamilton_24_tiprack_4000uL_filter.__name__,
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
      make_tip=hamilton_tip_4000uL_filter,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 5 ml Tips # # # # # # # # # #


def hamilton_24_tiprack_5000uL(name: str, with_tips: bool = True) -> TipRack:
  """Hamilton cat. no.: 184020 (non-sterile), 184022 (sterile)
  Hamilton name: 'FivemlT'
  Tip Rack 24x 5ml Tip landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model=hamilton_24_tiprack_5000uL.__name__,
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
      make_tip=hamilton_tip_5000uL,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # Deprecation Warnings # # # # # # # # # #

# TODO: remove after December 2025 (giving approx. 3 month transition period)


def LTF(name: str) -> TipRack:
  raise NotImplementedError("LTF is deprecated. use hamilton_96_tiprack_10uL_filter instead")


def LT(name: str) -> TipRack:
  raise NotImplementedError("LT is deprecated. use hamilton_96_tiprack_10uL instead")


def TIP_50ul_w_filter(name: str) -> TipRack:
  raise NotImplementedError(
    "TIP_50ul_w_filter is deprecated. use hamilton_96_tiprack_50uL_filter instead"
  )


def TIP_50ul(name: str) -> TipRack:
  raise NotImplementedError("TIP_50ul is deprecated. use hamilton_96_tiprack_50uL instead")


def STF(name: str) -> TipRack:
  raise NotImplementedError("STF is deprecated. use hamilton_96_tiprack_300uL_filter instead")


def ST(name: str) -> TipRack:
  raise NotImplementedError("ST is deprecated. use hamilton_96_tiprack_300uL instead")


def STF_Slim(name: str) -> TipRack:
  raise NotImplementedError(
    "STF_Slim is deprecated. use hamilton_96_tiprack_300uL_filter_slim instead"
  )


def HTF(name: str) -> TipRack:
  raise NotImplementedError("HTF is deprecated. use hamilton_96_tiprack_1000uL_filter instead")


def HT(name: str) -> TipRack:
  raise NotImplementedError("HT is deprecated. use hamilton_96_tiprack_1000uL instead")
