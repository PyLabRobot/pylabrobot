""" Abstract base class for tip rack resources. """

from abc import ABCMeta

from typing import List, Union, Optional, Sequence

from pylabrobot.liquid_handling.tip_tracker import SpotTipTracker
from pylabrobot.liquid_handling.tip_type import TipType

from .itemized_resource import ItemizedResource
from .resource import Resource


class TipSpot(Resource):
  """ A tip spot, a location in a tip rack where there may or may not be a tip. """

  def __init__(self, name: str, size_x: float, size_y: float, size_z: float = 0,
    start_with_tip: bool = True, category: str = "tip_spot"):
    """ Initialize a tip spot.

    Args:
      name: the name of the tip spot.
      size_x: the size of the tip spot in the x direction.
      size_y: the size of the tip spot in the y direction.
      category: the category of the tip spot.
    """

    super().__init__(name, size_x=size_y, size_y=size_x, size_z=size_z,
      category=category)
    self.tracker = SpotTipTracker(start_with_tip=start_with_tip)
    self.parent: Optional["TipRack"] = None

  @property
  def tip_type(self) -> TipType:
    assert self.parent is not None, "TipSpot must have a parent TipRack."
    return self.parent.tip_type


class TipRack(ItemizedResource[TipSpot], metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    tip_type: TipType,
    items: Optional[List[List[TipSpot]]] = None,
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
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  def get_tip(self, identifier: Union[str, int]) -> TipSpot:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier)

  def get_tips(self, identifier: Union[str, Sequence[int]]) -> List[TipSpot]:
    """ Get the tips with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return super().get_items(identifier)

  def set_tip_state(self, tips: List[List[bool]]) -> None:
    """ Set the initial tip tracking state of all tips in this tip rack. """

    # flatten the list
    has_tip = [item for sublist in tips for item in sublist]

    for i in range(self.num_items):
      self.get_item(i).tracker.set_initial_state(has_tip[i])
