"""ML Star tip types

The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

import enum
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
      maximal_volume=data["maximal_volume"],
      tip_size=TipSize[data["tip_size"]],
      pickup_method=TipPickupMethod[data["pickup_method"]],
    )


def standard_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Standard volume tip without a filter (`tt00` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=59.9,  # 60 in the ctr file, but 59.9 in the log file (519-80)/10
    maximal_volume=400,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Standard volume tip without a filter (`tt01` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=59.9,  # 60 in the ctr file, but 59.9 in the log file (519+80)/10
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def slim_standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Slim standard volume tip without a filter"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=94.8,
    maximal_volume=360,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def ultrawide_standard_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Ultra wide bore (1.55 mm) standard volume tip with a filter"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=51.9,
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def low_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """Low volume tip with a filter (`tt02` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=29.9,
    maximal_volume=15,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def low_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Low volume tip with a filter  (`tt03` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=29.9,
    maximal_volume=10,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def high_volume_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """High volume tip without a filter (`tt04` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=95.1,
    maximal_volume=1250,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """High volume tip with a filter (`tt05` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=95.1,  # 95 in the ctr file, but 95.1 in the log file (871-80)/10
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def wide_high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Wide bore (1.20 mm) high volume tip with a filter, Hamilton P/N 235677"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=91.95,  # Measured
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def ultrawide_high_volume_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """Ultra wide bore (3.20 mm) high volume tip with a filter, Hamilton P/N 235541"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=80.0,  # Measured
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def four_ml_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """4mL tip with a filter (`tt29` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=116,
    maximal_volume=4367,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def five_ml_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """5mL tip with a filter (`tt25` in venus)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=116,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def five_ml_tip(name: Optional[str] = None) -> HamiltonTip:
  """5mL tip without a filter (`tt25` in venus)

  This tip type is copied from five_ml_tip_with_filter, but the filter is set to False. I'm not sure
  where the actual thing is defined.
  """

  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=116,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def fifty_ul_tip_with_filter(name: Optional[str] = None) -> HamiltonTip:
  """50 ul tip with a filter (Hamilton cat. no.: 235948)"""
  return HamiltonTip(
    name=name,
    has_filter=True,
    total_tip_length=50.4,
    maximal_volume=60,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )


def fifty_ul_tip_no_filter(name: Optional[str] = None) -> HamiltonTip:
  """50 ul tip with a filter (Hamilton cat. no.: 235966)"""
  return HamiltonTip(
    name=name,
    has_filter=False,
    total_tip_length=50.4,
    maximal_volume=65,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK,
  )
