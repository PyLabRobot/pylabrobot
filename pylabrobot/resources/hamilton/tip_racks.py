from pylabrobot.resources.tip import TipCreator
from pylabrobot.resources.tip_rack import (
  EmbeddedTipRack,
  StandingTipRack,
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


def hamilton_tiprack_standard(
  name: str, make_tip: TipCreator, with_tips: bool = True
) -> EmbeddedTipRack:
  """Universal tip rack for Hamilton STAR systems."""
  return EmbeddedTipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=hamilton_tiprack_standard.__name__,
    sinking_depth=6.0,  # sinking depth
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      # The spot is the hole the tip drops into: 7.2 mm across, measured on the rack's own model,
      # where the tip's 8.2 mm collar comes to rest. dx and dy carry the corner of that square, so
      # they sit 0.9 mm inside the 9 mm pitch and every spot keeps its centre.
      dx=8.1,
      dy=6.2,
      dz=7.7 - 0.2,  # depth of hole
      item_dx=9.0,
      item_dy=9.0,
      size_x=7.2,
      size_y=7.2,
      make_tip=make_tip,
      name_prefix=name,
    ),
    with_tips=with_tips,
    frame_height=10.0,
  )


def hamilton_nested_tiprack(
  name: str, make_tip: TipCreator, with_tips: bool = True
) -> StandingTipRack:
  """Nested tip rack (NTR) for Hamilton STAR systems, the same body for every tip it carries.

  The body is its measured model, 127.35 x 84.8 x 56 mm. Venus declares the rack by a 122.4 x
  82.6 footprint, the site it stands in, with A1 11.7 mm in from the left and 9.8 mm from the back
  of that site; the model's holes sit there to within 0.25 mm when the model is centred on the
  site, so the spots are placed from Venus' numbers on the centred body.
  """
  return StandingTipRack(
    name=name,
    size_x=127.35,
    size_y=84.8,
    size_z=56.0,
    model=hamilton_nested_tiprack.__name__,
    # One rack nested on another stands 16 mm higher: Venus' StackHt, and the 48 mm between its
    # NTR1 and NTR4 carrier sites is three of them.
    # TODO: measure a nest on the instrument; Venus does not model nests, so nothing has checked it.
    stacking_z_height=16.0,
    ordered_items=create_ordered_items_2d(
      TipSpot,
      num_items_x=12,
      num_items_y=8,
      # The spot is the 7.2 mm hole the tip drops into, measured on the rack's model (Venus: 6.8).
      # A1's centre is 11.7 + (127.35 - 122.4) / 2 from the left and H1's 82.6 - 9.8 - 63 +
      # (84.8 - 82.6) / 2 from the front, less half a spot.
      dx=11.7 + (127.35 - 122.4) / 2 - 3.6,
      dy=82.6 - 9.8 - 7 * 9.0 + (84.8 - 82.6) / 2 - 3.6,
      # The tip's collar rests 55 mm above the bottom of the rack: firmware spot z 184.0 with the
      # rack standing at 129.0 on hamilton_tip_carrier_L5_ntr_a00, for the 10, 50 and 300 uL racks
      # alike. The model's top is at 56.
      # TODO: find the 1 mm between the model's top and where the collar rests.
      dz=55.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=7.2,
      size_y=7.2,
      make_tip=make_tip,
      name_prefix=name,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # 10 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_10uL_filter(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235936 (sterile), 235901 (non-sterile)
  Hamilton name: 'LTF'
  Tip Rack with 96x 10ul Low Volume Tip with filter
  """
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_10uL_filter, with_tips=with_tips
  )


def hamilton_96_tiprack_10uL(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235900 (non-sterile) 235935 (sterile)
  Hamilton name: 'LT'
  Tip Rack with 96x 10ul Low Volume Tip"""
  return hamilton_tiprack_standard(name=name, make_tip=hamilton_tip_10uL, with_tips=with_tips)


def hamilton_96_tiprack_10uL_NTR(name: str, with_tips: bool = True) -> StandingTipRack:
  """Hamilton cat. no.: 235949 (non-sterile), 235971 (clear, non-sterile), 235983 (sterile)
  Hamilton name: 'LT_L_NE_stack'
  Nested Tip Rack with 96x 10ul Low Volume Tip"""
  return hamilton_nested_tiprack(name=name, make_tip=hamilton_tip_10uL, with_tips=with_tips)


# # # # # # # # # # 50 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_50uL_filter(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235948 (non-sterile), 235979 (sterile), 235829 (clear, non-sterile)
  Hamilton name: 'TIP_50ul_w_filter'
  Tip Rack with 96x 50ul Tip"""
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_50uL_filter, with_tips=with_tips
  )


def hamilton_96_tiprack_50uL(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235966 (non-sterile) 235978 (sterile)
  Hamilton name: 'TIP_50ul'
  Tip Rack with 96x 50ul Tip no filter"""
  return hamilton_tiprack_standard(name=name, make_tip=hamilton_tip_50uL, with_tips=with_tips)


def hamilton_96_tiprack_50uL_NTR(name: str, with_tips: bool = True) -> StandingTipRack:
  """Hamilton cat. no.: 235947 (non-sterile), 235964 (clear, non-sterile), 235987 (sterile)
  Hamilton name: 'TIP_50ul_L_NE_stack'
  Nested Tip Rack with 96x 50ul Tip no filter"""
  return hamilton_nested_tiprack(name=name, make_tip=hamilton_tip_50uL, with_tips=with_tips)


# # # # # # # # # # 300 ul Tips # # # # # # # # # #


def hamilton_96_tiprack_300uL_filter(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235830 (clear, non-sterile), 235903 (non-sterile), 235938 (sterile)
  Hamilton name: 'STF'
  Tip Rack with 96x 300ul Standard Volume Tip with filter"""
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_300uL_filter, with_tips=with_tips
  )


def hamilton_96_tiprack_300uL(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235834 (clear, non-sterile), 235902 (non-sterile), 235937 (sterile)
  Hamilton name: 'ST'
  Tip Rack with 96x 300ul Standard Volume Tip"""
  return hamilton_tiprack_standard(name=name, make_tip=hamilton_tip_300uL, with_tips=with_tips)


def hamilton_96_tiprack_300uL_NTR(name: str, with_tips: bool = True) -> StandingTipRack:
  """Hamilton cat. no.: 235950 (non-sterile), 235965 (clear, non-sterile), 235985 (sterile)
  Hamilton name: 'ST_L_NE_stack'
  Nested Tip Rack with 96x 300ul Standard Volume Tip"""
  return hamilton_nested_tiprack(name=name, make_tip=hamilton_tip_300uL, with_tips=with_tips)


def hamilton_96_tiprack_300uL_filter_slim(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235646 (CORE-II: conductive)
  Hamilton name: 'STF_Slim'
  Tip Rack with 96x 300ul Slim Standard Volume Tip with filter"""
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_300uL_filter_slim, with_tips=with_tips
  )


def hamilton_96_tiprack_300uL_filter_ultrawide(
  name: str, with_tips: bool = True
) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235449 (1.55 mm oriface, non-sterile)
  Hamilton name: 'STF'
  Tip Rack with 96x 300ul Wide Bore Standard Volume Tip with filter"""
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_300uL_filter_ultrawide, with_tips=with_tips
  )


# # # # # # # # # # 1_000 uL Tips # # # # # # # # # #


def hamilton_96_tiprack_1000uL_filter(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235820 (clear, non-sterile), 235905 (non-sterile), 235940 (sterile)
  Hamilton name: 'HTF'
  Tip Rack with 96x 1000ul High Volume Tip with filter
  """
  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_1000uL_filter, with_tips=with_tips
  )


def hamilton_96_tiprack_1000uL(name: str, with_tips: bool = True) -> EmbeddedTipRack:
  """Hamilton cat. no.: 235822 (clear, non-sterile), 235904 (non-sterile), 235939 (sterile)
  Hamilton name: 'HT'
  Tip Rack with 96x 1000ul High Volume Tip"""
  return hamilton_tiprack_standard(name=name, make_tip=hamilton_tip_1000uL, with_tips=with_tips)


def hamilton_96_tiprack_1000uL_filter_wide(name: str, with_tips: bool = True) -> EmbeddedTipRack:
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

  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_1000uL_filter_wide, with_tips=with_tips
  )


def hamilton_96_tiprack_1000uL_filter_ultrawide(
  name: str, with_tips: bool = True
) -> EmbeddedTipRack:
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

  return hamilton_tiprack_standard(
    name=name, make_tip=hamilton_tip_1000uL_filter_ultrawide, with_tips=with_tips
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
      name_prefix=name,
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
      name_prefix=name,
    ),
    with_tips=with_tips,
  )


# # # # # # # # # # Deprecation Warnings # # # # # # # # # #

# TODO: remove after December 2025 (giving approx. 3 month transition period)


def LTF(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("LTF is deprecated. use hamilton_96_tiprack_10uL_filter instead")


def LT(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("LT is deprecated. use hamilton_96_tiprack_10uL instead")


def TIP_50ul_w_filter(name: str) -> EmbeddedTipRack:
  raise NotImplementedError(
    "TIP_50ul_w_filter is deprecated. use hamilton_96_tiprack_50uL_filter instead"
  )


def TIP_50ul(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("TIP_50ul is deprecated. use hamilton_96_tiprack_50uL instead")


def STF(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("STF is deprecated. use hamilton_96_tiprack_300uL_filter instead")


def ST(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("ST is deprecated. use hamilton_96_tiprack_300uL instead")


def STF_Slim(name: str) -> EmbeddedTipRack:
  raise NotImplementedError(
    "STF_Slim is deprecated. use hamilton_96_tiprack_300uL_filter_slim instead"
  )


def HTF(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("HTF is deprecated. use hamilton_96_tiprack_1000uL_filter instead")


def HT(name: str) -> EmbeddedTipRack:
  raise NotImplementedError("HT is deprecated. use hamilton_96_tiprack_1000uL instead")
