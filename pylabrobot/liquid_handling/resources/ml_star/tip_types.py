""" ML Star tip types

The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

from pylabrobot.liquid_handling.resources.abstract import (
  TipType,
  TIP_TYPE_LOW_VOLUME,
  TIP_TYPE_STANDARD_VOLUME,
  TIP_TYPE_HIGH_VOLUME,
  TIP_TYPE_XL_CHANNEL
)


__all__ = [
  "standard_volume_tip_no_filter",
  "standard_volume_tip_with_filter",
  "low_volume_tip_no_filter",
  "low_volume_tip_with_filter",
  "high_volume_tip_no_filter",
  "high_volume_tip_with_filter",
  "four_ml_tip_with_filter",
  "five_ml_tip_with_filter",
  "five_ml_tip"
]


#: Standard volume tip without a filter (`tt00` in venus)
standard_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=400,
  tip_type_id=TIP_TYPE_STANDARD_VOLUME,
  pick_up_method=0
)

#: Low volume tip without a filter (`tt01` in venus)
standard_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=360,
  tip_type_id=TIP_TYPE_STANDARD_VOLUME,
  pick_up_method=0
)

#: Standard volume tip with a filter (`tt02` in venus)
low_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=29.9,
  maximal_volume=15,
  tip_type_id=TIP_TYPE_LOW_VOLUME,
  pick_up_method=0
)

#: Low volume tip with a filter  (`tt03` in venus)
low_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=29.9,
  maximal_volume=10,
  tip_type_id=TIP_TYPE_LOW_VOLUME,
  pick_up_method=0
)

#: High volume tip without a filter (`tt04` in venus)
high_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=59.9,
  maximal_volume=1000,
  tip_type_id=TIP_TYPE_HIGH_VOLUME,
  pick_up_method=0
)

#: High volume tip with a filter (`tt05` in venus)
high_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=95.1, # 95 in the ctr file, but 95.1 in the log file (871-80)/10
  maximal_volume=1250,
  tip_type_id=TIP_TYPE_HIGH_VOLUME,
  pick_up_method=0
)

#: 4mL tip with a filter (`tt29` in venus)
four_ml_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=4367,
  tip_type_id=TIP_TYPE_XL_CHANNEL,
  pick_up_method=0
)

#: 5mL tip with a filter (`tt25` in venus)
five_ml_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=5420,
  tip_type_id=TIP_TYPE_XL_CHANNEL,
  pick_up_method=0
)

#: 5mL tip without a filter (`tt25` in venus)
# This tip type is copied from five_ml_tip_with_filter, but the filter is set to False. I'm not sure
# where the actual thing is defined.
five_ml_tip = TipType(
  has_filter=False,
  total_tip_length=116,
  maximal_volume=5420,
  tip_type_id=TIP_TYPE_XL_CHANNEL,
  pick_up_method=0
)
