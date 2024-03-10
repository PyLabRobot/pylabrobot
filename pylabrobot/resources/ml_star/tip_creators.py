""" ML Star tip types

The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

import enum
from typing import Union

from pylabrobot.resources.tip import Tip


class TipSize(enum.Enum):
  """ Tip type. These correspond to the tip types in the FW documentation (see command TT) """
  UNDEFINED=0
  LOW_VOLUME=1
  STANDARD_VOLUME=2
  HIGH_VOLUME=3
  CORE_384_HEAD_TIP=4
  XL=5


class TipPickupMethod(enum.Enum):
  """ Tip pickup method """
  OUT_OF_RACK=0
  OUT_OF_WASH_LIQUID=1


class TipDropMethod(enum.Enum):
  """ Tip drop method """
  PLACE_SHIFT=0
  DROP=1


class HamiltonTip(Tip):
  """ Represents a single tip for Hamilton instruments. """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_size: Union[TipSize, str], # union for deserialization, will probably refactor
    pickup_method: Union[TipPickupMethod, str] # union for deserialization, will probably refactor
  ):
    if isinstance(tip_size, str):
      tip_size = TipSize[tip_size]
    if isinstance(pickup_method, str):
      pickup_method = TipPickupMethod[pickup_method]

    fitting_depth = {
      None: 0,
      0: 0,
      1: 8,
      2: 8,
      3: 8,
      4: 7.55,
      5: 10,
      6: 8
    }[tip_size.value]

    super().__init__(
      total_tip_length=total_tip_length,

      has_filter=has_filter,
      maximal_volume=maximal_volume,
      fitting_depth=fitting_depth)

    self.pickup_method = pickup_method
    self.tip_size = tip_size

  def __repr__(self) -> str:
    return f"HamiltonTip({self.tip_size.name}, " \
            f"has_filter={self.has_filter}, " \
            f"maximal_volume={self.maximal_volume}, " \
            f"fitting_depth={self.fitting_depth}, " \
            f"total_tip_length={self.total_tip_length}, " \
            f"pickup_method={self.pickup_method.name})"

  def __hash__(self):
    return hash(repr(self))

  def get_uid(self) -> int:
    """ Get a unique identifier for the general information in the tip. (2 tips of the same class,
    say high volume with filter, should return the same value) """

    return hash((self.tip_size.name, self.has_filter, self.maximal_volume, self.fitting_depth,
                 self.pickup_method.name, self.total_tip_length))

  def serialize(self):
    super_serialized = super().serialize()
    del super_serialized["fitting_depth"] # inferred from tip size
    return {
      **super_serialized,
      "pickup_method": self.pickup_method.name,
      "tip_size": self.tip_size.name
    }

  @classmethod
  def deserialize(cls, data):
    return HamiltonTip(
      has_filter=data["has_filter"],
      total_tip_length=data["total_tip_length"],
      maximal_volume=data["maximal_volume"],
      tip_size=TipSize[data["tip_size"]],
      pickup_method=TipPickupMethod[data["pickup_method"]]
    )


def standard_volume_tip_no_filter() -> HamiltonTip:
  """ Standard volume tip without a filter (`tt00` in venus) """
  return HamiltonTip(
    has_filter=False,
    total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
    maximal_volume=400,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def standard_volume_tip_with_filter() -> HamiltonTip:
  """ Low volume tip without a filter (`tt01` in venus) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
    maximal_volume=360,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def low_volume_tip_no_filter() -> HamiltonTip:
  """ Standard volume tip with a filter (`tt02` in venus) """
  return HamiltonTip(
    has_filter=False,
    total_tip_length=29.9,
    maximal_volume=15,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def low_volume_tip_with_filter() -> HamiltonTip:
  """ Low volume tip with a filter  (`tt03` in venus) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=29.9,
    maximal_volume=10,
    tip_size=TipSize.LOW_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def high_volume_tip_no_filter() -> HamiltonTip:
  """ High volume tip without a filter (`tt04` in venus) """
  return HamiltonTip(
    has_filter=False,
    total_tip_length=95.1,
    maximal_volume=1250,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def high_volume_tip_with_filter() -> HamiltonTip:
  """ High volume tip with a filter (`tt05` in venus) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=95.1, # 95 in the ctr file, but 95.1 in the log file (871-80)/10
    maximal_volume=1065,
    tip_size=TipSize.HIGH_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def four_ml_tip_with_filter() -> HamiltonTip:
  """ 4mL tip with a filter (`tt29` in venus) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=116,
    maximal_volume=4367,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def five_ml_tip_with_filter() -> HamiltonTip:
  """ 5mL tip with a filter (`tt25` in venus) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=116,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def five_ml_tip() -> HamiltonTip:
  """ 5mL tip without a filter (`tt25` in venus)

  This tip type is copied from five_ml_tip_with_filter, but the filter is set to False. I'm not sure
  where the actual thing is defined.
  """

  return HamiltonTip(
    has_filter=False,
    total_tip_length=116,
    maximal_volume=5420,
    tip_size=TipSize.XL,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def fifty_ul_tip_with_filter() -> HamiltonTip:
  """ 50 ul tip with a filter (Hamilton cat. no.: 235948) """
  return HamiltonTip(
    has_filter=True,
    total_tip_length=50.4,
    maximal_volume=60,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )

def fifty_ul_tip_no_filter() -> HamiltonTip:
  """ 50 ul tip with a filter (Hamilton cat. no.: 235966) """
  return HamiltonTip(
    has_filter=False,
    total_tip_length=50.4,
    maximal_volume=65,
    tip_size=TipSize.STANDARD_VOLUME,
    pickup_method=TipPickupMethod.OUT_OF_RACK
  )
