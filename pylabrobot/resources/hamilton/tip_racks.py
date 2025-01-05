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


def FourmlTF(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack 24x 4ml Tip with Filter landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model="FourmlTF",
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


def FourmlTF_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "FourmlTF instead.")


def FourmlTF_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "FourmlTF().rotated(z=90) instead."
  )


def FivemlT(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack 24x 5ml Tip landscape oriented"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=7.0,
    model="FivemlT",
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


def FivemlT_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "FivemlT instead.")


def FivemlT_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "FivemlT().rotated(z=90) instead."
  )


def HTF(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 1000ul High Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="HTF",
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


def HTF_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "HTF instead.")


def HTF_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "HTF().rotated(z=90) instead."
  )


def HTF_WIDE(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 1000ul High Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=HTF_WIDE.__name__,
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


def HTF_L_WIDE(name: str, with_tips: bool = True) -> TipRack:
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "HTF_WIDE instead.")


def HTF_ULTRAWIDE(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 1000ul High Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=HTF_ULTRAWIDE.__name__,
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


def HTF_L_ULTRAWIDE(name: str, with_tips: bool = True) -> TipRack:
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "HTF_ULTRAWIDE instead.")


def HT(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 1000ul High Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="HT",
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


def HT_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "HT instead.")


def HT_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "HT().rotated(z=90) instead."
  )


def LTF(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 10ul Low Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="LTF",
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


def LTF_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "LTF instead.")


def LTF_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "LTF().rotated(z=90) instead."
  )


def LT(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 10ul Low Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="LT",
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


def LT_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "LT instead.")


def LT_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "LT().rotated(z=90) instead."
  )


def STF(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 300ul Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="STF",
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


def STF_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "STF instead.")


def STF_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "STF().rotated(z=90) instead."
  )


def STF_Slim(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 300ul Slim Standard Volume Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model=STF_Slim_L.__name__,
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


def STF_Slim_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "STF_Slim instead.")


def STF_Slim_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "STF_Slim().rotated(z=90) instead."
  )


def ST(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 300ul Standard Volume Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=20.0,
    model="ST",
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


def ST_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "ST instead.")


def ST_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "ST().rotated(z=90) instead."
  )


def TIP_50ul_w_filter(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 50ul Tip with filter"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="TIP_50ul_w_filter",
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


def TIP_50ul_w_filter_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "TIP_50ul_w_filter instead."
  )


def TIP_50ul_w_filter_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "TIP_50ul_w_filter().rotated(z=90) instead."
  )


def TIP_50ul(name: str, with_tips: bool = True) -> TipRack:
  """Tip Rack with 96 50ul Tip"""
  return TipRack(
    name=name,
    size_x=122.4,
    size_y=82.6,
    size_z=18.0,
    model="TIP_50ul",
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


def TIP_50ul_L(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError("_L and _P definitions are deprecated. Use " "TIP_50ul instead.")


def TIP_50ul_P(name: str, with_tips: bool = True) -> TipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "TIP_50ul().rotated(z=90) instead."
  )


def Hamilton_96_tiprack_50ul_NTR(name: str, with_tips: bool = True) -> NestedTipRack:
  """Nested Tip Rack with 96 50ul Tip"""
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


def Hamilton_96_tiprack_50ul_NTR_L(name: str, with_tips: bool = True) -> NestedTipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use " "Hamilton_96_tiprack_50ul_NTR instead."
  )


def Hamilton_96_tiprack_50ul_NTR_P(name: str, with_tips: bool = True) -> NestedTipRack:
  # https://github.com/PyLabRobot/pylabrobot/issues/252
  raise NotImplementedError(
    "_L and _P definitions are deprecated. Use "
    "Hamilton_96_tiprack_50ul_NTR().rotated(z=90) instead."
  )
