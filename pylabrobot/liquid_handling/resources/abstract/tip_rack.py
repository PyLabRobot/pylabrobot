""" Abstract base class for tip rack resources. """

from abc import ABCMeta

from typing import List, Union, Optional, Sequence

from .itemized_resource import ItemizedResource
from .tip import Tip
from .tip_type import TipType


class TipRack(ItemizedResource[Tip], metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
    items: Optional[List[List[Tip]]] = None,
    num_items_x: Optional[int] = None,
    num_items_y: Optional[int] = None,
    category: str = "tip_rack",
  ):
    super().__init__(name, size_x, size_y, size_z, items=items,
                     num_items_x=num_items_x, num_items_y=num_items_y, category=category)
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

  def get_tips(self, identifier: Union[str, Sequence[int]]) -> List[Tip]:
    """ Get the tips with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)
