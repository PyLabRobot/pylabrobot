"""ML Star tip types

The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

import enum
import warnings
from typing import Optional, Union

from pylabrobot.resources.tip import Tip


class TipSize(enum.Enum):
  """Tip type. These correspond to the tip types in the FW documentation (see command TT)"""

  UNDEFINED = 0
  LOW_VOLUME = 1  # i.e. tip_collar_size_z == 6 mm
  STANDARD_VOLUME = 2  # i.e. tip_collar_size_z == 8 mm
  HIGH_VOLUME = 3  # i.e. tip_collar_size_z == 10 mm
  CORE_384_HEAD_TIP = 4  # TODO: identify tip_collar_size_z
  XL = 5  # TODO: identify tip_collar_size_z


class TipPickupMethod(enum.Enum):
  """Tip pickup method"""

  OUT_OF_RACK = 0
  OUT_OF_WASH_LIQUID = 1


class TipDropMethod(enum.Enum):
  """Tip drop method"""

  PLACE_SHIFT = 0
  DROP = 1


class HamiltonTip(Tip):
  """Represents a single tip for Hamilton instruments."""

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_size: Union[TipSize, str],  # union for deserialization, will probably refactor
    pickup_method: Union[TipPickupMethod, str],  # union for deserialization, will probably refactor
    nominal_volume: Optional[float] = None,
    name: Optional[str] = None,
  ):
    if isinstance(tip_size, str):
      tip_size = TipSize[tip_size]
    if isinstance(pickup_method, str):
      pickup_method = TipPickupMethod[pickup_method]

    fitting_depth = {
      None: 0,
      TipSize.UNDEFINED: 0,
      TipSize.LOW_VOLUME: 8,
      TipSize.STANDARD_VOLUME: 8,
      TipSize.HIGH_VOLUME: 8,
      TipSize.CORE_384_HEAD_TIP: 7.55,
      TipSize.XL: 10,
      6: 8,
    }[tip_size]

    super().__init__(
      total_tip_length=total_tip_length,
      has_filter=has_filter,
      nominal_volume=nominal_volume,
      maximal_volume=maximal_volume,
      fitting_depth=fitting_depth,
      name=name,
    )

    self.pickup_method = pickup_method
    self.tip_size = tip_size

  def __repr__(self) -> str:
    name_field = f"'{self.name}'" if self.name is not None else "None"
    return (
      f"HamiltonTip(name={name_field}, "
      f"tip_size={self.tip_size.name}, "
      f"has_filter={self.has_filter}, "
      f"nominal_volume={self.nominal_volume}, "
      f"maximal_volume={self.maximal_volume}, "
      f"fitting_depth={self.fitting_depth}, "
      f"total_tip_length={self.total_tip_length}, "
      f"pickup_method={self.pickup_method.name})"
    )

  def serialize(self):
    super_serialized = super().serialize()
    del super_serialized["fitting_depth"]  # inferred from tip size
    return {
      **super_serialized,
      "pickup_method": self.pickup_method.name,
      "tip_size": self.tip_size.name,
    }

  @classmethod
  def deserialize(cls, data):
    return HamiltonTip(
      name=data["name"],
      has_filter=data["has_filter"],
      total_tip_length=data["total_tip_length"],
      nominal_volume=data.get("nominal_volume"),
      maximal_volume=data["maximal_volume"],
      tip_size=TipSize[data["tip_size"]],
      pickup_method=TipPickupMethod[data["pickup_method"]],
    )


def standard_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_300uL` instead."""
  warnings.warn(
    "standard_volume_tip_no_filter is deprecated, use hamilton_tip_300uL instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_300uL(name=name)


def standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_300uL_filter` instead."""
  warnings.warn(
    "standard_volume_tip_with_filter is deprecated, use hamilton_tip_300uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_300uL_filter(name=name)


def slim_standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_300uL_filter_slim` instead."""
  warnings.warn(
    "slim_standard_volume_tip_with_filter is deprecated, use hamilton_tip_300uL_filter_slim instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_300uL_filter_slim(name=name)


def ultrawide_standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_300uL_filter_ultrawide` instead."""
  warnings.warn(
    "ultrawide_standard_volume_tip_with_filter is deprecated, "
    "use hamilton_tip_300uL_filter_ultrawide instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_300uL_filter_ultrawide(name=name)


def low_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_10uL` instead."""
  warnings.warn(
    "low_volume_tip_no_filter is deprecated, use hamilton_tip_10uL instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_10uL(name=name)


def low_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_10uL_filter` instead."""
  warnings.warn(
    "low_volume_tip_with_filter is deprecated, use hamilton_tip_10uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_10uL_filter(name=name)


def high_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_1000uL` instead."""
  warnings.warn(
    "high_volume_tip_no_filter is deprecated, use hamilton_tip_1000uL instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_1000uL(name=name)


def high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_1000uL_filter` instead."""
  warnings.warn(
    "high_volume_tip_with_filter is deprecated, use hamilton_tip_1000uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_1000uL_filter(name=name)


def wide_high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_1000uL_filter_wide` instead."""
  warnings.warn(
    "wide_high_volume_tip_with_filter is deprecated, use hamilton_tip_1000uL_filter_wide instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_1000uL_filter_wide(name=name)


def ultrawide_high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_1000uL_filter_ultrawide` instead."""
  warnings.warn(
    "ultrawide_high_volume_tip_with_filter is deprecated, "
    "use hamilton_tip_1000uL_filter_ultrawide instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_1000uL_filter_ultrawide(name=name)


def four_ml_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_4000uL_filter` instead."""
  warnings.warn(
    "four_ml_tip_with_filter is deprecated, use hamilton_tip_4000uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_4000uL_filter(name=name)


def five_ml_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_5000uL_filter` instead."""
  warnings.warn(
    "five_ml_tip_with_filter is deprecated, use hamilton_tip_5000uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_5000uL_filter(name=name)


def five_ml_tip(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_5000uL` instead."""
  warnings.warn(
    "five_ml_tip is deprecated, use hamilton_tip_5000uL instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_5000uL(name=name)


def fifty_ul_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_50uL_filter` instead."""
  warnings.warn(
    "fifty_ul_tip_with_filter is deprecated, use hamilton_tip_50uL_filter instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_50uL_filter(name=name)


def fifty_ul_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Deprecated. Use :func:`hamilton_tip_50uL` instead."""
  warnings.warn(
    "fifty_ul_tip_no_filter is deprecated, use hamilton_tip_50uL instead",
    DeprecationWarning,
    stacklevel=2,
  )
  return hamilton_tip_50uL(name=name)


# # # # # # # # # # New naming convention # # # # # # # # # #


def hamilton_tip_10uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 10 uL tip without filter (`tt02` in venus)

  Variants:
    - Hamilton cat. no.: 235935 - black/conductive, framed EmbeddedTipRack, sterile
    - Hamilton cat. no.: 235900 - black/conductive, framed EmbeddedTipRack, non-sterile
    - Hamilton cat. no.: 235983 - black/conductive, nested StandingTipRack, sterile
    - Hamilton cat. no.: 235949 - black/conductive, nested StandingTipRack, non-sterile
    - Hamilton cat. no.: 235971 - transparent, nested StandingTipRack, non-sterile
    - Hamilton cat. no.: 235932 - steel (single tip)
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=29.9,
    nominal_volume=10,
    maximal_volume=15,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_10uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 10 uL tip with filter (`tt03` in venus)

  Variants:
    - Hamilton cat. no.: 235936 - transparent, framed EmbeddedTipRack, sterile
    - Hamilton cat. no.: 235901 - transparent, framed EmbeddedTipRack, non-sterile
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=29.9,
    nominal_volume=10,
    maximal_volume=10,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_50uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 50 uL tip without filter

  Variants:
    - Hamilton cat. no.: 235978 - black/conductive, framed EmbeddedTipRack, sterile
    - Hamilton cat. no.: 235966 - black/conductive, framed EmbeddedTipRack, non-sterile
    - Hamilton cat. no.: 235987 - black/conductive, nested StandingTipRack, sterile
    - Hamilton cat. no.: 235947 - black/conductive, nested StandingTipRack, non-sterile
    - Hamilton cat. no.: 235964 - transparent, nested StandingTipRack, non-sterile
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=50.4,
    nominal_volume=50,
    maximal_volume=65,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_50uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 50 uL tip with filter

  Hamilton cat. no.: 235948
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=50.4,
    nominal_volume=50,
    maximal_volume=60,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_300uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 300 uL tip without filter (`tt00` in venus)

  Variants:
    - Hamilton cat. no.: 235937 - black/conductive, framed EmbeddedTipRack, sterile
    - Hamilton cat. no.: 235965 - transparent, nested StandingTipRack, non-sterile
    - Hamilton cat. no.: 235931 - steel (single tip)
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=59.9,
    nominal_volume=300,
    maximal_volume=400,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_300uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 300 uL tip with filter (`tt01` in venus)

  Variants:
    - Hamilton cat. no.: 235938 - black/conductive, framed EmbeddedTipRack, sterile
    - Hamilton cat. no.: 235903 - black/conductive, framed EmbeddedTipRack, non-sterile
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=59.9,
    nominal_volume=300,
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_300uL_filter_slim(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 300 uL slim tip with filter"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=94.8,
    nominal_volume=300,
    maximal_volume=360,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_300uL_filter_ultrawide(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 300 uL ultra wide bore (1.55 mm) tip with filter"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=51.9,
    nominal_volume=300,
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_1000uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 1000 uL tip without filter

  Variants:
    - Hamilton cat. no.: 235939 - black/conductive, framed tiprack, sterile
    - Hamilton cat. no.: 235822 - transparent, framed tiprack, non-sterile
    - Hamilton cat. no.: 235930 - steel (single tip)
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=95.1,
    nominal_volume=1000,
    maximal_volume=1250,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_1000uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 1000 uL tip with filter

  Hamilton cat. no.: 235940 - conductive, sterile
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=95.1,
    nominal_volume=1000,
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_1000uL_filter_wide(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 1000 uL wide bore (1.20 mm) tip with filter

  Hamilton P/N 235677
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=91.95,
    nominal_volume=1000,
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_1000uL_filter_ultrawide(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 1000 uL ultra wide bore (3.20 mm) tip with filter

  Hamilton P/N 235541
  """
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=80.0,
    nominal_volume=1000,
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_4000uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 4000 uL tip with filter (`tt29` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=116,
    nominal_volume=4000,
    maximal_volume=4367,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_5000uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 5000 uL tip without filter (`tt25` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=116,
    nominal_volume=5000,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_tip_5000uL_filter(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton 5000 uL tip with filter (`tt25` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=116,
    nominal_volume=5000,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


# # # # # # # # # # Teaching needles # # # # # # # # # #


def hamilton_teaching_needle_300uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton teaching needle, 300 uL size class - closed-tip probe, cannot pipette.

  For labware teaching, not liquid handling. Geometry matches hamilton_tip_300uL; the
  working volume is zeroed so any attempt to aspirate raises.

  Hamilton cat. no.: 182176 (set of 8: 182136)
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=59.9,
    nominal_volume=0,
    maximal_volume=0,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def hamilton_teaching_needle_5000uL(name: Optional[str] = None) -> HamiltonTip:
  """Hamilton teaching needle, 5000 uL size class - closed-tip probe, cannot pipette.

  For labware teaching, not liquid handling. Geometry matches hamilton_tip_5000uL; the
  working volume is zeroed so any attempt to aspirate raises.

  Hamilton cat. no.: 184184
  """
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=116,
    nominal_volume=0,
    maximal_volume=0,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


# TODO: model the CoRe grip tools (cat. 186100, and the XL-channel gripper 171840) as
# HamiltonTip tool definitions the same way as the teaching needles above
# (maximal_volume=0; the define_tip_needle floor sends the 1.0 uL the firmware uses for
# its grip tools). Routing pick_up_core_gripper_tools through get_or_assign_tip_type_index
# would then drop the hardcoded tt="14" and remove the collision risk where a dynamically
# assigned tip type can land on index 14 and overwrite the grip-tool definition.
