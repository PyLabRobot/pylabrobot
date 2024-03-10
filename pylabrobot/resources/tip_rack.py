""" Abstract base class for tip rack resources. """

from __future__ import annotations

from abc import ABCMeta
from typing import Any, Dict, List, Union, Optional, Sequence, cast

from pylabrobot.resources.tip import Tip, TipCreator
from pylabrobot.resources.tip_tracker import TipTracker, does_tip_tracking
from pylabrobot.serializer import deserialize

from .itemized_resource import ItemizedResource
from .resource import Resource


class TipSpot(Resource):
  """ A tip spot, a location in a tip rack where there may or may not be a tip. """

  def __init__(self, name: str, size_x: float, size_y: float, make_tip: TipCreator,
    size_z: float = 0, category: str = "tip_spot"):
    """ Initialize a tip spot.

    Args:
      name: the name of the tip spot.
      size_x: the size of the tip spot in the x direction.
      size_y: the size of the tip spot in the y direction.
      size_z: the size of the tip spot in the z direction.
      make_tip: a function that creates a tip for the tip spot.
      category: the category of the tip spot.
    """

    super().__init__(name, size_x=size_y, size_y=size_x, size_z=size_z,
      category=category)
    self.tracker = TipTracker(thing="Tip spot")
    self.parent: Optional["TipRack"] = None

    self.make_tip = make_tip

    self.tracker.register_callback(self._state_updated)

  def get_tip(self) -> Tip:
    """ Get a tip from the tip spot. """

    # Tracker will raise an error if there is no tip. We spawn a new tip if tip tracking is disabled
    tracks = does_tip_tracking() and not self.tracker.is_disabled
    if not self.tracker.has_tip and not tracks:
      self.tracker.add_tip(self.make_tip())

    return self.tracker.get_tip()

  def has_tip(self) -> bool:
    """ Check if the tip spot has a tip. """
    return self.tracker.has_tip

  def empty(self) -> None:
    """ Empty the tip spot. """
    self.tracker.remove_tip()

  def serialize(self) -> dict:
    """ Serialize the tip spot. """
    return {
      **super().serialize(),
      "prototype_tip": self.make_tip().serialize(),
    }

  @classmethod
  def deserialize(cls, data: dict) -> TipSpot:
    """ Deserialize a tip spot. """
    tip_data = data["prototype_tip"]
    def make_tip() -> Tip:
      return cast(Tip, deserialize(tip_data))

    return cls(
      name=data["name"],
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data["size_z"],
      make_tip=make_tip,
      category=data.get("category", "tip_spot")
    )

  def serialize_state(self) -> Dict[str, Any]:
    return self.tracker.serialize()

  def load_state(self, state: Dict[str, Any]):
    self.tracker.load_state(state)


class TipRack(ItemizedResource[TipSpot], metaclass=ABCMeta):
  """ Abstract base class for Tips resources. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    items: Optional[List[List[TipSpot]]] = None,
    num_items_x: Optional[int] = None,
    num_items_y: Optional[int] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
  ):
    super().__init__(name, size_x, size_y, size_z, items=items, num_items_x=num_items_x,
      num_items_y=num_items_y, category=category, model=model)

    if items is not None and len(items) > 0:
      if with_tips:
        self.fill()
      else:
        self.empty()

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  def get_tip(self, identifier: Union[str, int]) -> Tip:
    """ Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier).get_tip()

  def get_tips(self, identifier: Union[str, Sequence[int]]) -> List[Tip]:
    """ Get the tips with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return [ts.get_tip() for ts in super().get_items(identifier)]

  def set_tip_state(self, tips: List[List[bool]]) -> None:
    """ Set the initial tip tracking state of all tips in this tip rack.

    Examples:
      Filling the right half of a 96-well tip rack:

      >>> tip_rack.set_tip_state([[True] * 6 + [False] * 6] * 8)
    """

    for i in range(self.num_items_y):
      for j in range(self.num_items_x):
        # If the tip state is different from the current state, update it by either creating or
        # removing the tip.
        if tips[i][j] and not self.get_item((i, j)).has_tip():
          self.get_item((i, j)).tracker.add_tip(self.get_item((i, j)).make_tip(), commit=True)
        elif not tips[i][j] and self.get_item((i, j)).has_tip():
          self.get_item((i, j)).tracker.remove_tip(commit=True)

  def disable_tip_trackers(self) -> None:
    """ Disable tip tracking for all tips in this tip rack. """
    for item in self.get_all_items():
      item.tracker.disable()

  def enable_tip_trackers(self) -> None:
    """ Enable tip tracking for all tips in this tip rack. """
    for item in self.get_all_items():
      item.tracker.enable()

  def empty(self):
    """ Empty the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot). """
    self.set_tip_state([[False] * self.num_items_x] * self.num_items_y)

  def fill(self):
    """ Fill the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot). """
    self.set_tip_state([[True] * self.num_items_x] * self.num_items_y)

  def get_all_tips(self) -> List[Tip]:
    """ Get all tips in the tip rack. """
    return [ts.get_tip() for ts in self.get_all_items()]
