from pylabrobot.liquid_handling.tip_tracker import SpotTipTracker

from .resource import Resource
from .tip_type import TipType


class Tip(Resource):
  """ A single Tip resource.

  .. note:: Currently, this can better be thought of as a tip spot, as it is a location where tips
    are stored.  However, in the future, this will be a single tip, and the tip spot will be a
    location in a tip rack.
  """

  def __init__(self, name: str, size_x: float, size_y: float, tip_type: TipType,
    category: str = "tip", has_tip: bool = True):
    super().__init__(name, size_x=size_y, size_y=size_x, size_z=tip_type.tip_length,
      category=category)
    self.tip_type = tip_type
    self.tracker = SpotTipTracker(start_with_tip=has_tip)

  def serialize(self) -> dict:
    serialized_parent = super().serialize()
    serialized_parent.pop("size_z") # tip_length is already in tip_type

    return {
      **serialized_parent,
      "tip_type": self.tip_type.serialize(),
      "has_tip": self.tracker.has_tip,
    }

  @classmethod
  def deserialize(cls, data):
    data["tip_type"] = TipType.deserialize(data["tip_type"])
    return super().deserialize(data)
