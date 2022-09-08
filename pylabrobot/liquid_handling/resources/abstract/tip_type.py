""" TipType object and identifiers """

from __future__ import annotations

from abc import ABCMeta


TIP_TYPE_UNDEFINED = 0
TIP_TYPE_LOW_VOLUME = 1
TIP_TYPE_STANDARD_VOLUME = 2
TIP_TYPE_HIGH_VOLUME = 3
TIP_TYPE_CORE_384 = 4
TIP_TYPE_XL_CHANNEL = 5


class TipType(object, metaclass=ABCMeta):
  """ TipType class

  Attributes:
    has_filter: whether the tip type has a filter
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
    self.tip_length = total_tip_length - fitting_depth

  def __eq__(self, other: TipType) -> bool:
    return (
      self.has_filter == other.has_filter and
      self.total_tip_length == other.total_tip_length and
      self.maximal_volume == other.maximal_volume and
      self.tip_type_id == other.tip_type_id and
      self.pick_up_method == other.pick_up_method
    )

  def __hash__(self) -> int:
    return hash(self.__repr__())

  def __repr__(self) -> str:
    return (f"TipType(has_filter={self.has_filter}, total_tip_length={self.total_tip_length}, "
            f"maximal_volume={self.maximal_volume}, tip_type_id={self.tip_type_id}, "
            f"pick_up_method={self.pick_up_method})")

  def serialize(self) -> dict:
    return {
      "has_filter": self.has_filter,
      "total_tip_length": self.total_tip_length,
      "maximal_volume": self.maximal_volume,
      "tip_type_id": self.tip_type_id,
      "pick_up_method": self.pick_up_method
    }

  @classmethod
  def deserialize(cls, data: dict) -> TipType:
    return cls(
      has_filter=data["has_filter"],
      total_tip_length=data["total_tip_length"],
      maximal_volume=data["maximal_volume"],
      tip_type_id=data["tip_type_id"],
      pick_up_method=data["pick_up_method"]
    )
