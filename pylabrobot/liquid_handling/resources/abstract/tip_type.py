""" TipType object and identifiers """

from __future__ import annotations

from abc import ABC
import enum


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


class TipType(ABC):
  """ TipType class

  TODO: there is some Hamilton specific stuff here, should be moved to Hamilton specific code.

  Attributes:
    has_filter: whether the tip type has a filter
    total_tip_length: total length of the tip, in in mm
    maximal_volume: maximal volume of the tip, in ul
    tip_size: id of the tip type.
    pick_up_method: pick up method of the tip type.
    filter_length: length of the filter, in 0.1mm, used in TT command. Tip lengths are calculated as
      follows: total tip length-fitting depth
  """

  def __init__(
    self,
    has_filter: bool,
    total_tip_length: float,
    maximal_volume: float,
    tip_size: TipSize,
    pick_up_method: TipPickupMethod
  ):
    self.has_filter = has_filter
    self.total_tip_length = total_tip_length
    self.maximal_volume = maximal_volume
    self.tip_size = tip_size
    self.pick_up_method = pick_up_method

    fitting_depth = {
      None: 0,
      0: 0,
      1: 8,
      2: 8,
      3: 8,
      4: 7.55,
      5: 10
    }[tip_size.value]
    self.tip_length = total_tip_length - fitting_depth

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, TipType) and
      self.has_filter == other.has_filter and
      self.total_tip_length == other.total_tip_length and
      self.maximal_volume == other.maximal_volume and
      self.tip_size == other.tip_size and
      self.pick_up_method == other.pick_up_method
    )

  def __hash__(self) -> int:
    return hash(self.__repr__())

  def __repr__(self) -> str:
    return (f"TipType(has_filter={self.has_filter}, total_tip_length={self.total_tip_length}, "
            f"maximal_volume={self.maximal_volume}, tip_size={self.tip_size}, "
            f"pick_up_method={self.pick_up_method})")

  def serialize(self) -> dict:
    return {
      "has_filter": self.has_filter,
      "total_tip_length": self.total_tip_length,
      "maximal_volume": self.maximal_volume,
      "tip_size": self.tip_size.value,
      "pick_up_method": self.pick_up_method.value
    }

  @classmethod
  def deserialize(cls, data: dict) -> TipType:
    return cls(
      has_filter=data["has_filter"],
      total_tip_length=data["total_tip_length"],
      maximal_volume=data["maximal_volume"],
      tip_size=TipSize(data["tip_size"]),
      pick_up_method=TipPickupMethod(data["pick_up_method"])
    )
