"""
The tip length is probably defined by the LSHt field in .ctr files. The values here are
obtained from by manually measuring and looking at the log files (TT commands in particular).
"""


TIP_TYPE_UNDEFINED = 0
TIP_TYPE_LOW_VOLUME = 1
TIP_TYPE_STANDARD_VOLUME = 2
TIP_TYPE_HIGH_VOLUME = 3
TIP_TYPE_CORE_384 = 4
TIP_TYPE_XL_CHANNEL = 5


class TipType:
  """ TipType class

  Properties:
    filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in in mm
    maximal_volume: maximal volume of the tip, in ul
    tip_type_id: id of the tip type. 0 = undefined, 1 = low volue, 2 = standard volume,
      3 = high volume, 4 = CoRe 384 tip, 5 = XL channel tip
    pick_up_method: pick up method of the tip type. 0 = out of rack, 1 = out of wash liquid
    filter_length: length of the filter, in 0.1mm, used in TT command. Tip lengths are calculated as
      follows: total tip length-fitting depth
  """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_type_id: int,
    pick_up_method: int
  ):
    self.has_filter = has_filter
    self.total_tip_length = total_tip_length
    self.maximal_volume = maximal_volume
    self.tip_type_id = tip_type_id
    self.pick_up_method = pick_up_method

    fitting_depth = {
      1: 8,
      2: 8,
      3: 8,
      4: 7.55,
      5: 10
    }[tip_type_id]
    self.tip_length = (total_tip_length - fitting_depth) * 10 # convert to 0.1mm units

  def __eq__(self, other):
    return (
      self.has_filter == other.has_filter and
      self.total_tip_length == other.total_tip_length and
      self.maximal_volume == other.maximal_volume and
      self.tip_type_id == other.tip_type_id and
      self.pick_up_method == other.pick_up_method
    )


standard_volume_tip_filter = TipType(
  has_filter=True,
  total_tip_length=59.9, # 60 in the ctr file, but 59.9 in the log file (519+80)/10
  maximal_volume=360,
  tip_type_id=TIP_TYPE_STANDARD_VOLUME,
  pick_up_method=0
)


high_volume_tip_filter = TipType(
  has_filter=True,
  total_tip_length=87.1, # 95 in the ctr file, but 95.1 in the log file (871+80)/10
  maximal_volume=1250,
  tip_type_id=TIP_TYPE_HIGH_VOLUME,
  pick_up_method=0
)
