""" Abstract base class for Tips resources. """

from abc import ABCMeta

from typing import List, Union

from .itemized_resource import ItemizedResource
from .resource import Resource, Coordinate
from .tip_type import TipType


class Tip(Resource):
  def __init__(self, name: str, size_x: float, size_y: float, tip_type: TipType,
    location: Coordinate = ..., category: str = "tip"):
    super().__init__(name, size_x, size_y, tip_type.tip_length, location, category)
    self.tip_type = tip_type


class Tips(ItemizedResource[Tip], metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
    dx: float,
    dy: float,
    dz: float,
    tip_size_x: float,
    tip_size_y: float,
    num_tips_x: int,
    num_tips_y: int,
    location: Coordinate = Coordinate(None, None, None)
  ):
    super().__init__(name, size_x, size_y, size_z, location=location,
                     category="tips",
                     num_items_x=num_tips_x, num_items_y=num_tips_y,
                     create_item=lambda i, j: Tip(
                        name=f"{self.name}_{i}_{j}",
                        size_x=tip_size_x, size_y=tip_size_y, tip_type=tip_type,
                        location=Coordinate(dx + i * tip_size_x, dy + (num_tips_y-j-1) * tip_size_y, dz)))
    self.tip_type = tip_type
    self.dx = dx
    self.dy = dy
    self.dz = dz

    self.tip_size_x = tip_size_x
    self.tip_size_y = tip_size_y

  def serialize(self):
    return dict(
      **super().serialize(),
      tip_type=self.tip_type.serialize(),
      dx=self.dx,
      dy=self.dy,
      dz=self.dz,
      tip_size_x=self.tip_size_x,
      tip_size_y=self.tip_size_y,
    )

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, tip_type={self.tip_type}, "
            f"dx={self.dx}, dy={self.dy}, dz={self.dz}, location={self.location})")

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
