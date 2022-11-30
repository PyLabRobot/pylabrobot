""" Abstract base class for tip rack resources. """

from abc import ABCMeta

from typing import List, Union, Optional, Sequence

from pylabrobot.liquid_handling.tip_tracker import SpotTipTracker
from pylabrobot.liquid_handling.tip_type import TipType
from pylabrobot import utils

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
    with_tips: bool = True,
  ):
    super().__init__(name, size_x, size_y, size_z, items=items,
                     num_items_x=num_items_x, num_items_y=num_items_y, category=category)
    self.tip_type = tip_type

    if items is not None and len(items) > 0:
      if with_tips:
        self.fill()
      else:
        self.empty()

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

  def set_tip_state(self, tips: Union[List[List[bool]], str]) -> None:
    """ Set the initial tip tracking state of all tips in this tip rack.

    Examples:
      Filling the left half of a 96-well tip rack:

      >>> tip_rack.set_tip_state("A7:H12")

      Filling the right half of a 96-well tip rack:

      >>> tip_rack.set_tip_state([[True] * 6 + [False] * 6] * 8)
    """

    if isinstance(tips, str):
      tips = utils.string_to_pattern(tips, num_rows=self.num_items_y, num_columns=self.num_items_x)

    # flatten the list
    has_tip = [item for sublist in tips for item in sublist]
    assert len(has_tip) == self.num_items, "Invalid tip state."

    for i in range(self.num_items):
      self.get_item(i).tracker.set_initial_state(has_tip[i])

  def empty(self):
    """ Empty the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot). """
    self.set_tip_state([[False] * self.num_items_x] * self.num_items_y)

  def fill(self):
    """ Fill the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot). """
    self.set_tip_state([[True] * self.num_items_x] * self.num_items_y)
