""" ML Star tip types

The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

import enum

from pylabrobot.liquid_handling.tip_type import TipType


class TipSize(enum.Enum):
  """ Tip type """
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


class HamiltonTipType(TipType):
  """ Represents a single tip for Hamilton instruments. """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_size: TipSize,
    pickup_method: TipPickupMethod
  ):
    fitting_depth = {
      None: 0,
      0: 0,
      1: 8,
      2: 8,
      3: 8,
      4: 7.55,
      5: 10
    }[tip_size.value]

    super().__init__(
      total_tip_length=total_tip_length,

      has_filter=has_filter,
      maximal_volume=maximal_volume,
      fitting_depth=fitting_depth)

    self.pickup_method = pickup_method
    self.tip_size = tip_size


# TODO: Can we compress this further?

#: Standard volume tip without a filter (`tt00` in venus)
standard_volume_tip_no_filter = HamiltonTipType(
  has_filter=False,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=400,
  tip_size=TipSize.STANDARD_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: Low volume tip without a filter (`tt01` in venus)
standard_volume_tip_with_filter = HamiltonTipType(
  has_filter=True,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=360,
  tip_size=TipSize.STANDARD_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: Standard volume tip with a filter (`tt02` in venus)
low_volume_tip_no_filter = HamiltonTipType(
  has_filter=False,
  total_tip_length=29.9,
  maximal_volume=15,
  tip_size=TipSize.LOW_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: Low volume tip with a filter  (`tt03` in venus)
low_volume_tip_with_filter = HamiltonTipType(
  has_filter=True,
  total_tip_length=29.9,
  maximal_volume=10,
  tip_size=TipSize.LOW_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: High volume tip without a filter (`tt04` in venus)
high_volume_tip_no_filter = HamiltonTipType(
  has_filter=False,
  total_tip_length=95.1,
  maximal_volume=1000,
  tip_size=TipSize.HIGH_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: High volume tip with a filter (`tt05` in venus)
high_volume_tip_with_filter = HamiltonTipType(
  has_filter=True,
  total_tip_length=95.1, # 95 in the ctr file, but 95.1 in the log file (871-80)/10
  maximal_volume=1250,
  tip_size=TipSize.HIGH_VOLUME,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: 4mL tip with a filter (`tt29` in venus)
four_ml_tip_with_filter = HamiltonTipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=4367,
  tip_size=TipSize.XL,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: 5mL tip with a filter (`tt25` in venus)
five_ml_tip_with_filter = HamiltonTipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=5420,
  tip_size=TipSize.XL,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)

#: 5mL tip without a filter (`tt25` in venus)
# This tip type is copied from five_ml_tip_with_filter, but the filter is set to False. I'm not sure
# where the actual thing is defined.
five_ml_tip = HamiltonTipType(
  has_filter=False,
  total_tip_length=116,
  maximal_volume=5420,
  tip_size=TipSize.XL,
  pickup_method=TipPickupMethod.OUT_OF_RACK
)
