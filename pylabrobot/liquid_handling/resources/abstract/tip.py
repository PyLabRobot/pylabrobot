from .resource import Resource
from .tip_type import TipType


class Tip(Resource):
  """ A single Tip resource. """

  def __init__(self, name: str, size_x: float, size_y: float, tip_type: TipType,
    category: str = "tip"):
    super().__init__(name, size_x=size_y, size_y=size_x, size_z=tip_type.tip_length,
      category=category)
    self.tip_type = tip_type

  def serialize(self) -> dict:
    serialized_parent = super().serialize()
    serialized_parent.pop("size_z") # tip_length is already in tip_type

    return {
      **serialized_parent,
      "tip_type": self.tip_type.serialize()
    }

  @classmethod
  def deserialize(cls, data):
    data["tip_type"] = TipType.deserialize(data["tip_type"])
    return super().deserialize(data)

