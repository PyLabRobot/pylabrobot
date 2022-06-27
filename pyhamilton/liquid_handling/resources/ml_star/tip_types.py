"""
The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained by matching ids in the liquid editor with the log files (tt parameter matches).
See the TT command.
"""

from pyhamilton.liquid_handling.resources.abstract import (
  TipType,
  TIP_TYPE_LOW_VOLUME,
  TIP_TYPE_STANDARD_VOLUME,
  TIP_TYPE_HIGH_VOLUME,
  TIP_TYPE_XL_CHANNEL
)


# tt00 in venus
standard_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=400,
  tip_type_id=TIP_TYPE_STANDARD_VOLUME,
  pick_up_method=0
)

# tt01 in venus
standard_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519-80)/10
  maximal_volume=360,
  tip_type_id=TIP_TYPE_STANDARD_VOLUME,
  pick_up_method=0
)

# tt02 in venus
low_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=29.9,
  maximal_volume=15,
  tip_type_id=TIP_TYPE_LOW_VOLUME,
  pick_up_method=0
)

## tt03 in venus
low_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=29.9,
  maximal_volume=10,
  tip_type_id=TIP_TYPE_LOW_VOLUME,
  pick_up_method=0
)

# tt04 in venus
high_volume_tip_no_filter = TipType(
  has_filter=False,
  total_tip_length=59.9,
  maximal_volume=1000,
  tip_type_id=TIP_TYPE_HIGH_VOLUME,
  pick_up_method=0
)

# tt05 in venus
high_volume_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=95.1, # 95 in the ctr file, but 95.1 in the log file (871-80)/10
  maximal_volume=1250,
  tip_type_id=TIP_TYPE_HIGH_VOLUME,
  pick_up_method=0
)

# tt29 in venus
four_ml_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=4367,
  tip_type_id=TIP_TYPE_XL_CHANNEL,
  pick_up_method=0
)

# tt25 in venus
five_ml_tip_with_filter = TipType(
  has_filter=True,
  total_tip_length=116,
  maximal_volume=5420,
  tip_type_id=TIP_TYPE_XL_CHANNEL,
  pick_up_method=0
)
