from __future__ import annotations

import warnings
from abc import ABCMeta
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Union, cast

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.head_tool import HeadTool, move_tool, release_named_tool
from pylabrobot.resources.tip import Tip, TipCreator
from pylabrobot.resources.tip_tracking import does_tip_tracking

from .itemized_resource import ItemizedResource
from .lid import Lid, Liddable
from .resource import Resource
from .resource_stack import ResourceStack

if TYPE_CHECKING:
  from pylabrobot.legacy.tip_tracker import TipTracker


def _legacy_tracker(spot: "TipSpot") -> "TipTracker":
  """The legacy liquid handler's tracker for a spot: a view on its tip in the tree.

  TODO: Remove in v1, with the methods on `TipSpot` and `TipRack` that use it.
  """
  from pylabrobot.legacy.tip_tracker import tip_spot_tracker

  return tip_spot_tracker(spot)


def resting_location(holder: Resource, tip: Tip) -> Coordinate:
  """Where a tip rests in a tip spot: centred on it, its pick-up location `collar_height` above.

  Args:
    holder: the spot.
    tip: the tip resting in it.

  Returns:
    The tip's location, relative to the spot.
  """
  collar_height = tip.collar_height if tip.has_collar_height else 0.0
  pick_up = tip.pick_up_location or tip.get_anchor("c", "c", "t")
  return Coordinate(
    x=holder.get_size_x() / 2 - pick_up.x,
    y=holder.get_size_y() / 2 - pick_up.y,
    z=collar_height - pick_up.z,
  )


class TipSpot(Resource):
  """A tip spot, a location in a tip rack where there may or may not be a tip.

  Each tip produced by this spot is given a unique name based on the parent
  rack name, the spot name, and a per-spot counter (e.g. ``rack.A1#1``,
  ``rack.A1#2``), which is stored on the :class:`Tip` instance.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    make_tip: TipCreator,
    size_z: float = 0,
    category: str = "tip_spot",
    metadata: Optional[Mapping[str, Any]] = None,
  ):
    """Initialize a tip spot.

    Args:
      name: the name of the tip spot.
      size_x: the size of the tip spot in the x direction.
      size_y: the size of the tip spot in the y direction.
      size_z: the size of the tip spot in the z direction.
      make_tip: a function that creates a tip for the tip spot.
      category: the category of the tip spot.
      metadata: metadata for the tip spot.
    """

    super().__init__(
      name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      metadata=metadata,
    )
    self.parent: Optional["TipRack"] = None

    self._tip_counter: int = 0

    self._make_tip_func = make_tip

    # Whether this spot gives up and takes back tips, with tip tracking on. The tip itself is state
    # of the tree: this spot's child.
    self.tip_tracking_enabled = True

  def _comparable_children(self) -> List[Resource]:
    """Everything but the tip it is holding, which is state."""
    return [child for child in self.children if not isinstance(child, HeadTool)]

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    """Assign a child. A tip assigned here is this spot's tip.

    A tip given no location rests by its collar.

    Raises:
      HasTipError: If a tip is assigned to a spot already holding a different one.
    """
    if isinstance(resource, Tip):
      held = self.tip
      if held is not None and held is not resource:
        raise HasTipError(f"{self.name} already holds {held.name}")
      if location is None:
        location = resting_location(self, resource)
    super().assign_child_resource(resource, location=location, reassign=reassign)

  @property
  def tip(self) -> Optional[Tip]:
    """The tip this spot holds in the resource tree, or None if it holds none.

    Read from the tree alone, not from the tracker: a tip moved onto a channel is no longer here.
    """
    return next((child for child in self.children if isinstance(child, Tip)), None)

  @property
  def tracks_tips(self) -> bool:
    """Whether what this spot holds is tracked: tip tracking is on, and not disabled for this spot.

    A tracked spot gives up the tip it holds when one is picked up, and takes one back when one is
    dropped into it. An untracked spot is left as it is: it hands out a fresh tip every time, as a
    spot does with tip tracking off.
    """
    return does_tip_tracking() and self.tip_tracking_enabled

  def disable_tip_tracking(self) -> None:
    """Leave this spot as it is whatever is picked up from it or dropped into it."""
    self.tip_tracking_enabled = False

  def enable_tip_tracking(self) -> None:
    """Track this spot again, when tip tracking is on."""
    self.tip_tracking_enabled = True

  def tip_for_pickup(self) -> Tip:
    """The tip a channel picking up from this spot collects.

    Raises:
      NoTipError: If the spot is tracked and holds no tip.
    """
    if not self.tracks_tips:
      return self.make_tip()
    if self.tip is None:
      raise NoTipError(f"{self.name} holds no tip")
    return self.tip

  def assign_tip(self, tip: Tip) -> None:
    """Put a tip in this spot, resting by its collar, wherever it was before.

    Raises:
      HasTipError: If the spot already holds a tip.
    """
    if self.tip is not None:
      raise HasTipError(f"{self.name} already holds a tip")
    move_tool(tip, lambda: self.assign_child_resource(tip, location=resting_location(self, tip)))

  def unassign_tip(self) -> Tip:
    """Take the tip out of this spot.

    Returns:
      The tip, which belongs to nothing until it is assigned elsewhere.

    Raises:
      NoTipError: If the spot holds no tip.
    """
    tip = self.tip
    if tip is None:
      raise NoTipError(f"{self.name} holds no tip")
    self.unassign_child_resource(tip)
    return tip

  def _get_next_tip_name(self) -> str:
    """Generate a unique name for the next tip originating from this spot."""

    name = f"{self.name}#{self._tip_counter}"
    self._tip_counter += 1
    return name

  def make_tip(self) -> Tip:
    """Create a new tip instance for this spot and assign it a unique name."""

    return self._make_tip_func(self._get_next_tip_name())

  # -- legacy ------------------------------------------------------------------------------------
  # TODO: Remove in v1. The legacy liquid handler's view of this spot, with its pending
  # operations: `tip`, `tip_for_pickup`, `assign_tip` and `unassign_tip` read and move the tree.

  @property
  def tracker(self) -> "TipTracker":
    """Deprecated: the legacy liquid handler's tracker for this spot. Use `tip`."""
    return _legacy_tracker(self)

  def get_tip(self) -> Tip:
    """Deprecated: get a tip from the tip spot. Use `tip` or `tip_for_pickup`."""

    # Tracker will raise an error if there is no tip. We spawn a new tip if tip tracking is disabled
    tracker = _legacy_tracker(self)
    tracks = does_tip_tracking() and not tracker.is_disabled
    if not tracker.has_tip and not tracks:
      tracker.add_tip(self.make_tip(), origin=self)

    return tracker.get_tip()

  def has_tip(self) -> bool:
    """Deprecated: check if the tip spot has a tip. Use `tip`."""
    return _legacy_tracker(self).has_tip

  def empty(self) -> None:
    """Deprecated: empty the tip spot. Use `unassign_tip`."""
    _legacy_tracker(self).remove_tip()

  def serialize(self) -> dict:
    """Serialize the tip spot. Its tip is state, not a serialized child."""
    data = {
      **super().serialize(),
      "prototype_tip": self.make_tip().serialize(),
    }
    data.pop("children", None)
    return data

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> TipSpot:
    """Deserialize a tip spot."""
    tip_data = data["prototype_tip"]

    def make_tip(name: str) -> Tip:
      tip_data_with_name = {**tip_data, "name": name}
      return Tip.deserialize(tip_data_with_name, allow_marshal=allow_marshal)

    return cls(
      name=data["name"],
      size_x=data["size_x"],
      size_y=data["size_y"],
      size_z=data.get("size_z", 0),
      make_tip=make_tip,
      category=data.get("category", "tip_spot"),
      metadata=data.get("metadata"),
    )

  def serialize_state(self) -> Dict[str, Any]:
    """This spot's state, with the tip it holds in the tree: `tip`, `tip_state` and `pending_tip`.

    `pending_tip` is the tip held now. `tip` is the same one, unless the legacy liquid handler has an
    operation on this spot it has not yet committed.
    """
    return {**super().serialize_state(), **_legacy_tracker(self).serialize()}

  def load_state(self, state: Dict[str, Any]):
    """Load this spot's state: the saved `pending_tip` goes into it, as this spot's child.

    A tool of the same name held elsewhere in the same tree, on a channel say, gives way first.
    """
    super().load_state(state)
    tracker_state = {k: v for k, v in state.items() if k != "rotation"}
    pending = tracker_state.get("pending_tip")
    if pending is not None:
      release_named_tool(self.get_root(), pending["name"], keep=self.tip)
    _legacy_tracker(self).load_state(tracker_state)

  def get_identifier(self) -> str:
    """Get the (canonical) identifier, like `"A1"` of the tip spot in the parent tip rack. If the
    tip spot not in a tip rack, this will raise a ValueError."""

    if self.parent is None or not isinstance(self.parent, TipRack):
      raise ValueError("TipSpot must be in a tip rack to get its identifier.")

    return self.parent.get_child_identifier(self)


def tip_origin(tip: Tip, root: Resource) -> Optional[TipSpot]:
  """The tip spot a tip was made by, looked up in a tree.

  A spot names each tip it makes after itself, `rack_tipspot_A1#0` say, so the name leads back to it
  wherever the tip is now.

  Args:
    tip: the tip.
    root: the tree to look for the spot in, a deck say.

  Returns:
    The spot, or None if the tip's name names no spot in `root`.
  """
  spot_name, separator, _ = tip.name.rpartition("#")
  if not separator or not root.has_resource(spot_name):
    return None
  spot = root.get_resource(spot_name)
  return spot if isinstance(spot, TipSpot) else None


class TipRack(Liddable, ItemizedResource[TipSpot], metaclass=ABCMeta):
  """Tip rack for disposable tips."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
    frame_height: Optional[float] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      metadata=metadata,
    )
    self._frame_height = frame_height

    if ordered_items is not None and len(ordered_items) > 0:
      if with_tips:
        self.fill()
      else:
        self.empty()

  @property
  def frame_height(self) -> float:
    """Return frame_height, raising if it is None."""
    if self._frame_height is None:
      raise ValueError(f"frame_height is not defined for this tip rack: {self!r}")
    return self._frame_height

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, size_x={self._size_x}, "
      f"size_y={self._size_y}, size_z={self._size_z}, location={self.location})"
    )

  @property
  def _available_for_tip_handling(self) -> bool:
    """Whether nothing, a lid or another rack in its stack, sits on top of this rack."""
    if self.lid is not None:
      return False
    stack = self.parent
    return not (
      isinstance(stack, ResourceStack) and stack.direction == "z" and stack.children[-1] is not self
    )

  @staticmethod
  def _occupied_func(item: TipSpot):
    return "V" if item.has_tip() else "-"

  def get_tip(self, identifier: Union[str, int]) -> Tip:
    """Get the item with the given identifier.

    See :meth:`~.get_item` for more information.
    """

    return super().get_item(identifier).get_tip()

  def get_tips(self, identifier: Union[str, Sequence[int]]) -> List[Tip]:
    """Get the tips with the given identifier.

    See :meth:`~.get_items` for more information.
    """

    return [ts.get_tip() for ts in super().get_items(identifier)]

  def set_tip_state(self, tips: Union[List[bool], Dict[str, bool]]) -> None:
    """Set the initial tip tracking state of all tips in this tip rack.

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
      should_have = cast(Dict[Union[int, str], bool], tips)  # type?

    for identifier, should_have_tip in should_have.items():
      spot = self.get_item(identifier)
      tracker = _legacy_tracker(spot)
      if should_have_tip and not tracker.has_tip:
        tracker.add_tip(spot.make_tip(), origin=spot, commit=True)
      elif not should_have_tip and tracker.has_tip:
        tracker.remove_tip(commit=True)

  def disable_tip_tracking(self) -> None:
    """Disable tip tracking for every spot in this tip rack."""
    for item in self.get_all_items():
      item.disable_tip_tracking()

  def enable_tip_tracking(self) -> None:
    """Enable tip tracking for every spot in this tip rack."""
    for item in self.get_all_items():
      item.enable_tip_tracking()

  def disable_tip_trackers(self) -> None:
    """Deprecated: use `disable_tip_tracking`. TODO: Remove in v1"""
    warnings.warn(
      "TipRack.disable_tip_trackers is deprecated. Use 'disable_tip_tracking' instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.disable_tip_tracking()

  def enable_tip_trackers(self) -> None:
    """Deprecated: use `enable_tip_tracking`. TODO: Remove in v1"""
    warnings.warn(
      "TipRack.enable_tip_trackers is deprecated. Use 'enable_tip_tracking' instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    self.enable_tip_tracking()

  def empty(self):
    """Empty the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot)."""
    self.set_tip_state([False] * self.num_items)

  def fill(self):
    """Fill the tip rack. This is useful when tip tracking is enabled and you are modifying
    the state manually (without the robot)."""
    self.set_tip_state([True] * self.num_items)

  def get_all_tips(self) -> List[Tip]:
    """Get all tips in the tip rack."""
    return [ts.get_tip() for ts in self.get_all_items()]


class EmbeddedTipRack(TipRack):
  """The EmbeddedTipRack - this is what some might call a "standard" TipRack; they cannot stand on their own, they require an EmbeddedTipRackHolder at all times to be functional.

  have historically been referred to as FTRs (officially "framed tip rack" from Hamilton, sometimes we used to call them "floating tip racks".
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    sinking_depth: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
    frame_height: Optional[float] = None,
    metadata: Optional[Mapping[str, Any]] = None,
  ):
    """sinking_depth: the depth the tip rack sinks into the tip holder when placed inside it."""
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      with_tips=with_tips,
      frame_height=frame_height,
      metadata=metadata,
    )
    self.sinking_depth = sinking_depth

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "sinking_depth": self.sinking_depth,
      "frame_height": self._frame_height,
    }


class StandingTipRack(TipRack):
  """A tip rack that stands on its own rather than sinking into a holder.

  Racks that nest are stacked in a z-growing :class:`~pylabrobot.resources.ResourceStack`.

  Attributes:
    stacking_z_height: how far a nested rack stands above the one below it, in mm, or None if the
      rack does not nest.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
    frame_height: Optional[float] = None,
    stacking_z_height: Optional[float] = None,
  ):
    super().__init__(
      name,
      size_x,
      size_y,
      size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      with_tips=with_tips,
      metadata=metadata,
      frame_height=frame_height,
    )
    self.stacking_z_height = stacking_z_height

  def __repr__(self) -> str:
    return (
      f"{self.__class__.__name__}(name={self.name!r}, size_x={self._size_x}, "
      f"size_y={self._size_y}, size_z={self._size_z}, "
      f"stacking_z_height={self.stacking_z_height}, location={self.location})"
    )

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "frame_height": self._frame_height,
      "stacking_z_height": self.stacking_z_height,
    }


class NestedTipRack(StandingTipRack):
  """Deprecated. Use :class:`StandingTipRack` with a `stacking_z_height` instead."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    stacking_z_height: float,
    ordered_items: Optional[Dict[str, TipSpot]] = None,
    ordering: Optional[OrderedDict[str, str]] = None,
    category: str = "tip_rack",
    model: Optional[str] = None,
    with_tips: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
    frame_height: Optional[float] = None,
  ):
    warnings.warn(
      "NestedTipRack is deprecated, use StandingTipRack with a stacking_z_height instead",
      DeprecationWarning,
      stacklevel=2,
    )
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      ordered_items=ordered_items,
      ordering=ordering,
      category=category,
      model=model,
      with_tips=with_tips,
      metadata=metadata,
      frame_height=frame_height,
      stacking_z_height=stacking_z_height,
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    if isinstance(resource, NestedTipRack):
      location = location or Coordinate(0, 0, cast(float, self.stacking_z_height))
    elif not isinstance(resource, Lid):
      assert location is not None, "Location must be specified if resource is not a NestedTipRack."
    return super().assign_child_resource(resource, location=location, reassign=reassign)
