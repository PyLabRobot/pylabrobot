from __future__ import annotations

from abc import ABCMeta
from typing import Any, Dict, List, Union, Optional, Sequence, cast

from pylabrobot.resources.coordinate import Coordinate
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
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> TipSpot:
    """ Deserialize a tip spot. """
    tip_data = data["prototype_tip"]
    def make_tip() -> Tip:
      return cast(Tip, deserialize(tip_data, allow_marshal=allow_marshal))

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
  """ Tip rack for disposable tips. """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[List[str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
  ):
    super().__init__(name, size_x, size_y, size_z, ordered_items=ordered_items, ordering=ordering,
                     category=category, model=model)

    if ordered_items is not None and len(ordered_items) > 0:
      if with_tips:
        self.fill()
      else:
        self.empty()

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})")

  @staticmethod
  def _occupied_func(item: TipSpot):
    return "V" if item.has_tip() else "-"

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

  def set_tip_state(self, tips: Union[List[bool], Dict[str, bool]]) -> None:
    """ Set the initial tip tracking state of all tips in this tip rack.

    Args:
      tips: either a list of booleans (using integer indexing) or a dictionary of booleans (using
        string indexing) for whether each tip should be filled or empty.

    Examples:
      Filling the right half of a 96-well tip rack:

      >>> tip_rack.set_tip_state([[True] * 6 + [False] * 6] * 8)
    """

    should_have: Dict[Union[int, str], bool] = {}
    if isinstance(tips, list):
      for i, tip in enumerate(tips):
        should_have[i] = tip
    else:
      should_have = cast(Dict[Union[int, str], bool], tips) # type?

    for identifier, should_have_tip in should_have.items():
      if should_have_tip and not self.get_item(identifier).has_tip():
        self.get_item(identifier).tracker.add_tip(self.get_item(identifier).make_tip(), commit=True)
      elif not should_have_tip and self.get_item(identifier).has_tip():
        self.get_item(identifier).tracker.remove_tip(commit=True)

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
    self.set_tip_state([False] * self.num_items)

  def fill(self):
    """ Fill the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot). """
    self.set_tip_state([True] * self.num_items)

  def get_all_tips(self) -> List[Tip]:
    """ Get all tips in the tip rack. """
    return [ts.get_tip() for ts in self.get_all_items()]


class NestedTipRack(TipRack):
  """ A nested tip rack. """
  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    stacking_z_height: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[List[str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
  ):
    # Call the superclass constructor
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      with_tips=with_tips
    )

    self.stacking_z_height = stacking_z_height

  def __repr__(self) -> str:
    return (f"{self.__class__.__name__}(name={self.name}, size_x={self._size_x}, "
            f"size_y={self._size_y}, size_z={self._size_z}, "
            f"stacking_z_height={self.stacking_z_height}, location={self.location})")

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True
  ):
    if isinstance(resource, NestedTipRack):
      location = location or Coordinate(0, 0, self.stacking_z_height)
    else:
      assert location is not None, "Location must be specified if " + \
        "resource is not a NestedTipRack."
    return super().assign_child_resource(resource, location=location, reassign=reassign)
