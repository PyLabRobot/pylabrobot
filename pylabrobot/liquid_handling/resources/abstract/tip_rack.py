""" Abstract base class for Tips resources. """

from abc import ABCMeta

from typing import List, Union

from .itemized_resource import ItemizedResource
from .resource import Resource, Coordinate
from .tip_type import TipType


class Tip(Resource):
  """ A single Tip resource. """

  def __init__(self, name: str, size_x: float, size_y: float, tip_type: TipType,
    location: Coordinate = None, category: str = "tip"):
    super().__init__(name, size_x=size_y, size_y=size_x, size_z=tip_type.tip_length,
      location=location, category=category)
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


class TipRack(ItemizedResource[Tip], metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
    items: List[List[Tip]] = None,
    location: Coordinate = Coordinate(None, None, None),
    category: str = "tip_rack",
  ):
    super().__init__(name, size_x, size_y, size_z, location=location,
                     category=category, items=items)
    self.tip_type = tip_type

  def serialize(self):
    return dict(
      **super().serialize(),
      tip_type=self.tip_type.serialize(),
    )

  @classmethod
  def deserialize(cls, data):
    data["tip_type"] = TipType.deserialize(data["tip_type"])
    return super().deserialize(data)

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, tip_type={self.tip_type}, "
            f"location={self.location})")

  def get_tip(self, identifier: Union[str, int]) -> Tip:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_tips(self, identifier: Union[str, List[int]]) -> List[Tip]:
    """ Get the tips with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)
