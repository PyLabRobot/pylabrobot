"""Defines LiquidHandler class, the coordinator for liquid handling operations."""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import unittest.mock
import warnings
from typing import (
  Any,
  Awaitable,
  Callable,
  Dict,
  Generator,
  List,
  Literal,
  Optional,
  Sequence,
  Set,
  Tuple,
  Union,
  cast,
)

from pylabrobot.liquid_handling.errors import ChannelizedError
from pylabrobot.liquid_handling.strictness import (
  Strictness,
  get_strictness,
)
from pylabrobot.liquid_handling.utils import (
  get_tight_single_resource_liquid_op_offsets,
  get_wide_single_resource_liquid_op_offsets,
)
from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.plate_reading import PlateReader
from pylabrobot.resources import (
  Container,
  Coordinate,
  Deck,
  Lid,
  Plate,
  PlateAdapter,
  PlateHolder,
  Resource,
  ResourceHolder,
  ResourceStack,
  Tip,
  TipRack,
  TipSpot,
  TipTracker,
  Trash,
  Well,
  does_tip_tracking,
  does_volume_tracking,
)
from pylabrobot.resources.errors import HasTipError
from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import deserialize, serialize
from pylabrobot.tilting.tilter import Tilter

from .backends import LiquidHandlerBackend
from .standard import (
  Drop,
  DropTipRack,
  GripDirection,
  Mix,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)

logger = logging.getLogger("pylabrobot")


TipPresenceProbingMethod = Callable[
  [List[TipSpot], Optional[List[int]]],
  Awaitable[Dict[str, bool]],
]


class BlowOutVolumeError(Exception):
  pass


class LiquidHandler(Resource, Machine):
  """
  Front end for liquid handlers.

  This class is the front end for liquid handlers; it provides a high-level interface for
  interacting with liquid handlers. In the background, this class uses the low-level backend (
  defined in `pyhamilton.liquid_handling.backends`) to communicate with the liquid handler.
  """

  def __init__(
    self,
    backend: LiquidHandlerBackend,
    deck: Deck,
    default_offset_head96: Optional[Coordinate] = None,
    name: Optional[str] = None,
  ):
    """Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
      deck: Deck to use.
      default_offset_head96: Base offset applied to all 96-head operations.
      name: Name of the liquid handler. If not provided, defaults to ``lh_{deck.name}``.
    """

    Resource.__init__(
      self,
      name=name if name is not None else f"lh_{deck.name}",
      size_x=deck._size_x,
      size_y=deck._size_y,
      size_z=deck._size_z,
      category="liquid_handler",
    )
    Machine.__init__(self, backend=backend)

    self.backend: LiquidHandlerBackend = backend  # fix type

    self.deck = deck

    self.head: Dict[int, TipTracker] = {}
    self.head96: Dict[int, TipTracker] = {}
    self._default_use_channels: Optional[List[int]] = None

    self._blow_out_air_volume: Optional[List[Optional[float]]] = None

    # Default offset applied to all 96-head operations. Any offset passed to a 96-head method is
    # added to this value.
    self.default_offset_head96: Coordinate = default_offset_head96 or Coordinate.zero()

    # assign deck as only child resource, and set location of self to origin.
    self.location = Coordinate.zero()
    super().assign_child_resource(deck, location=deck.location or Coordinate.zero())

    self._resource_pickup: Optional[ResourcePickup] = None

  async def setup(self, **backend_kwargs):
    """Prepare the robot for use."""

    if self.setup_finished:
      raise RuntimeError("The setup has already finished. See `LiquidHandler.stop`.")

    self.backend.set_deck(self.deck)
    self.backend.set_heads(head=self.head, head96=self.head96)
    await super().setup(**backend_kwargs)

    self.head = {c: TipTracker(thing=f"Channel {c}") for c in range(self.backend.num_channels)}
    self.head96 = {c: TipTracker(thing=f"Channel {c}") for c in range(96)}

    self._resource_pickup = None

  def serialize_state(self) -> Dict[str, Any]:
    """Serialize the state of this liquid handler. Use :meth:`~Resource.serialize_all_states` to
    serialize the state of the liquid handler and all children (the deck)."""

    head_state = {channel: tracker.serialize() for channel, tracker in self.head.items()}
    return {"head_state": head_state}

  def load_state(self, state: Dict[str, Any]):
    """Load the liquid handler state from a file. Use :meth:`~Resource.load_all_state` to load the
    state of the liquid handler and all children (the deck)."""

    head_state = state["head_state"]
    for channel, tracker_state in head_state.items():
      self.head[channel].load_state(tracker_state)

  def update_head_state(self, state: Dict[int, Optional[Tip]]):
    """Update the state of the liquid handler head.

    All keys in `state` must be valid channels. Channels for which no key is specified will keep
    their current state.

    Args:
      state: A dictionary mapping channels to tips. If a channel is mapped to None, that channel
        will have no tip.
    """

    assert set(state.keys()).issubset(set(self.head.keys())), "Invalid channel."

    for channel, tip in state.items():
      if tip is None:
        if self.head[channel].has_tip:
          self.head[channel].remove_tip()
      else:
        if self.head[channel].has_tip:  # remove tip so we can update the head.
          self.head[channel].remove_tip()
        self.head[channel].add_tip(tip)

  def clear_head_state(self):
    """Clear the state of the liquid handler head."""

    self.update_head_state({c: None for c in self.head.keys()})

  def summary(self):
    """Prints a string summary of the deck layout."""

    print(self.deck.summary())

  def _assert_positions_unique(self, positions: List[str]):
    """Returns whether all items in `positions` are unique where they are not `None`.

    Args:
      positions: List of positions.
    """

    not_none = [p for p in positions if p is not None]
    if len(not_none) != len(set(not_none)):
      raise ValueError("Positions must be unique.")

  def _assert_resources_exist(self, resources: Sequence[Resource]):
    """Checks that each resource in `resources` is assigned to the deck.

    Args:
      resources: List of resources.

    Raises:
      ValueError: If a resource is not assigned to the deck.
    """

    for resource in resources:
      # names on the deck are unique, so we can simply check if the resource matches the one on
      # the deck (if any).
      resource_from_deck = self.deck.get_resource(resource.name)
      # it might be better to use `is`, but that would probably cause problems with autoreload.
      if not resource_from_deck == resource:
        raise ValueError(f"Resource {resource} is not assigned to the deck.")

  def _check_args(
    self,
    method: Callable,
    backend_kwargs: Dict[str, Any],
    default: Set[str],
    strictness: Strictness,
  ) -> Set[str]:
    """Checks that the arguments to `method` are valid.

    Args:
      method: Method to check.
      backend_kwargs: Keyword arguments to `method`.
      default: Default arguments to `method`. (Of the abstract backend)
      strictness: Strictness level. If `Strictness.STRICT`, raises an error if there are extra
        arguments. If `Strictness.WARN`, raises a warning. If `Strictness.IGNORE`, logs a debug
        message.

    Raises:
      TypeError: If the arguments are invalid.

    Returns:
      The set of arguments that need to be removed from `backend_kwargs` before passing to `method`.
    """

    # if method is an AsyncMock, skip the checks
    if isinstance(method, unittest.mock.AsyncMock):
      return set()

    default_args = default.union({"self"})

    sig = inspect.signature(method)
    args = {arg: param for arg, param in sig.parameters.items() if arg not in default_args}
    vars_keyword = {
      arg
      for arg, param in sig.parameters.items()  # **kwargs
      if param.kind == inspect.Parameter.VAR_KEYWORD
    }
    args = {
      arg: param
      for arg, param in args.items()  # keep only *args and **kwargs
      if param.kind
      not in {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
      }
    }
    non_default = {arg for arg, param in args.items() if param.default == inspect.Parameter.empty}

    backend_kws = set(backend_kwargs.keys())

    missing = non_default - backend_kws
    if len(missing) > 0:
      raise TypeError(f"Missing arguments to backend.{method.__name__}: {missing}")

    if len(vars_keyword) > 0:
      return set()  # no extra arguments if the method accepts **kwargs

    extra = backend_kws - set(args.keys())
    if len(extra) > 0 and len(vars_keyword) == 0:
      if strictness == Strictness.STRICT:
        raise TypeError(f"Extra arguments to backend.{method.__name__}: {extra}")
      elif strictness == Strictness.WARN:
        warnings.warn(f"Extra arguments to backend.{method.__name__}: {extra}")
      else:
        logger.debug("Extra arguments to backend.%s: %s", method.__name__, extra)

    return extra

  def _make_sure_channels_exist(self, channels: List[int]):
    """Checks that the channels exist."""
    invalid_channels = [c for c in channels if c not in self.head]
    if not len(invalid_channels) == 0:
      raise ValueError(f"Invalid channels: {invalid_channels}")

  def _format_param(self, value: Any) -> Any:
    """Format parameters for logging."""
    if isinstance(value, Resource):
      return value.name
    try:
      if isinstance(value, Sequence) and len(value) > 0 and isinstance(value[0], Resource):
        return [v.name for v in value]
    except Exception:
      pass
    return value

  def _log_command(self, name: str, **kwargs) -> None:
    params = ", ".join(f"{k}={self._format_param(v)}" for k, v in kwargs.items())
    logger.debug("%s(%s)", name, params)

  def get_picked_up_resource(self) -> Optional[Resource]:
    """Get the resource that is currently picked up.

    Returns:
      The resource that is currently picked up, or `None` if no resource is being picked up.
    """

    if self._resource_pickup is None:
      return None
    return self._resource_pickup.resource

  @need_setup_finished
  async def pick_up_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    **backend_kwargs,
  ):
    """Pick up tips from a resource.

    Examples:
      Pick up all tips in the first column.

      >>> await lh.pick_up_tips(tips_resource["A1":"H1"])

      Pick up tips on odd numbered rows, skipping the other channels.

      >>> await lh.pick_up_tips(tips_resource["A1", "C1", "E1", "G1"],use_channels=[0, 2, 4, 6])

      Pick up tips from different tip resources:

      >>> await lh.pick_up_tips(tips_resource1["A1"] + tips_resource2["B2"] + tips_resource3["C3"])

      Picking up tips with different offsets:

      >>> await lh.pick_up_tips(
      ...   tip_spots=tips_resource["A1":"C1"],
      ...   offsets=[
      ...     Coordinate(0, 0, 0), # A1
      ...     Coordinate(1, 1, 1), # B1
      ...     Coordinate.zero() # C1
      ...   ]
      ... )

    Args:
      tip_spots: List of tip spots to pick up tips from.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      offsets: List of offsets, one for each channel: a translation that will be applied to the tip
        drop location.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the positions are not unique.

      HasTipError: If a channel already has a tip.

      NoTipError: If a spot does not have a tip.
    """

    self._log_command(
      "pick_up_tips",
      tip_spots=tip_spots,
      use_channels=use_channels,
      offsets=offsets,
    )

    not_tip_spots = [ts for ts in tip_spots if not isinstance(ts, TipSpot)]
    if len(not_tip_spots) > 0:
      raise TypeError(f"Resources must be `TipSpot`s, got {not_tip_spots}")

    # fix arguments
    use_channels = use_channels or self._default_use_channels or list(range(len(tip_spots)))
    assert len(set(use_channels)) == len(use_channels), "Channels must be unique."

    tips = [tip_spot.get_tip() for tip_spot in tip_spots]

    if not all(
      self.backend.can_pick_up_tip(channel, tip) for channel, tip in zip(use_channels, tips)
    ):
      cannot = [
        channel
        for channel, tip in zip(use_channels, tips)
        if not self.backend.can_pick_up_tip(channel, tip)
      ]
      raise RuntimeError(f"Cannot pick up tips on channels {cannot}.")

    # expand default arguments
    offsets = offsets or [Coordinate.zero()] * len(tip_spots)

    # checks
    self._assert_resources_exist(tip_spots)
    self._make_sure_channels_exist(use_channels)
    assert (
      len(tip_spots) == len(offsets) == len(use_channels)
    ), "Number of tips and offsets and use_channels must be equal."

    # create operations
    pickups = [
      Pickup(resource=tip_spot, offset=offset, tip=tip)
      for tip_spot, offset, tip in zip(tip_spots, offsets, tips)
    ]

    # queue operations on the trackers
    for channel, op in zip(use_channels, pickups):
      if self.head[channel].has_tip:
        raise HasTipError("Channel has tip")
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        op.resource.tracker.remove_tip()
      self.head[channel].add_tip(op.tip, origin=op.resource, commit=False)

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.pick_up_tips,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    # actually pick up the tips
    error: Optional[Exception] = None
    try:
      await self.backend.pick_up_tips(ops=pickups, use_channels=use_channels, **backend_kwargs)
    except Exception as e:
      error = e

    # determine which channels were successful
    successes = [error is None] * len(pickups)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [channel_idx not in error.errors for channel_idx in use_channels]

    # commit or rollback the state trackers
    for channel, op, success in zip(use_channels, pickups, successes):
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
      (self.head[channel].commit if success else self.head[channel].rollback)()

    if error is not None:
      raise error

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Get the tips currently mounted on the head.

    Returns:
      A list of tips currently mounted on the head, or `None` for channels without a tip.
    """
    return [tracker.get_tip() if tracker.has_tip else None for tracker in self.head.values()]

  @need_setup_finished
  async def drop_tips(
    self,
    tip_spots: Sequence[Union[TipSpot, Trash]],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    allow_nonzero_volume: bool = False,
    **backend_kwargs,
  ):
    """Drop tips to a resource.

    Examples:
      Dropping tips to the first column.

      >>> await lh.pick_up_tips(tip_rack["A1:H1"])

      Dropping tips with different offsets:

      >>> await lh.drop_tips(
      ...   channels=tips_resource["A1":"C1"],
      ...   offsets=[
      ...     Coordinate(0, 0, 0), # A1
      ...     Coordinate(1, 1, 1), # B1
      ...     Coordinate.zero() # C1
      ...   ]
      ... )

    Args:
      tip_spots: Tip resource locations to drop to.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      offsets: List of offsets, one for each channel, a translation that will be applied to the tip
        drop location. If `None`, no offset will be applied.
      allow_nonzero_volume: If `True`, the tip will be dropped even if its volume is not zero (there
        is liquid in the tip). If `False`, a RuntimeError will be raised if the tip has nonzero
        volume.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None` or
        if the list of channels is empty.

      ValueError: If the positions are not unique.

      NoTipError: If a channel does not have a tip.

      HasTipError: If a spot already has a tip.
    """

    self._log_command(
      "drop_tips",
      tip_spots=tip_spots,
      use_channels=use_channels,
      offsets=offsets,
      allow_nonzero_volume=allow_nonzero_volume,
    )

    not_tip_spots = [ts for ts in tip_spots if not isinstance(ts, (TipSpot, Trash))]
    if len(not_tip_spots) > 0:
      raise TypeError(f"Resources must be `TipSpot`s or Trash, got {not_tip_spots}")

    # fix arguments
    use_channels = use_channels or self._default_use_channels or list(range(len(tip_spots)))
    assert len(set(use_channels)) == len(use_channels), "Channels must be unique."

    tips = []
    for channel in use_channels:
      tip = self.head[channel].get_tip()
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
        raise RuntimeError(f"Cannot drop tip with volume {tip.tracker.get_used_volume()}")
      tips.append(tip)

    # expand default arguments
    offsets = offsets or [Coordinate.zero()] * len(tip_spots)

    # checks
    self._assert_resources_exist(tip_spots)
    self._make_sure_channels_exist(use_channels)
    assert (
      len(tip_spots) == len(offsets) == len(use_channels) == len(tips)
    ), "Number of channels and offsets and use_channels and tips must be equal."

    # create operations
    drops = [
      Drop(resource=tip_spot, offset=offset, tip=tip)
      for tip_spot, tip, offset in zip(tip_spots, tips, offsets)
    ]

    # queue operations on the trackers
    for channel, op in zip(use_channels, drops):
      if (
        does_tip_tracking()
        and isinstance(op.resource, TipSpot)
        and not op.resource.tracker.is_disabled
      ):
        op.resource.tracker.add_tip(op.tip, commit=False)
      self.head[channel].remove_tip()

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.drop_tips,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    # actually drop the tips
    error: Optional[Exception] = None
    try:
      await self.backend.drop_tips(ops=drops, use_channels=use_channels, **backend_kwargs)
    except Exception as e:
      error = e

    # determine which channels were successful
    successes = [error is None] * len(drops)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [channel_idx not in error.errors for channel_idx in use_channels]

    # commit or rollback the state trackers
    for channel, op, success in zip(use_channels, drops, successes):
      if (
        does_tip_tracking()
        and isinstance(op.resource, TipSpot)
        and not op.resource.tracker.is_disabled
      ):
        (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
      (self.head[channel].commit if success else self.head[channel].rollback)()

    if error is not None:
      raise error

  async def return_tips(
    self,
    use_channels: Optional[list[int]] = None,
    allow_nonzero_volume: bool = False,
    offsets: Optional[List[Coordinate]] = None,
    **backend_kwargs,
  ):
    """Return all tips that are currently picked up to their original place.

    Examples:
      Return the tips on the head to the tip rack where they were picked up:

      >>> await lh.pick_up_tips(tip_rack["A1"])
      >>> await lh.return_tips()

    Args:
      use_channels: List of channels to use. Index from front to back. If `None`, all that have
        tips will be used.
      allow_nonzero_volume: If `True`, tips will be returned even if their volumes are not zero.
      backend_kwargs: backend kwargs passed to `drop_tips`.

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    self._log_command(
      "return_tips",
      use_channels=use_channels,
      allow_nonzero_volume=allow_nonzero_volume,
    )

    tip_spots: List[TipSpot] = []
    channels: List[int] = []

    for channel, tracker in self.head.items():
      if use_channels is not None and channel not in use_channels:
        continue
      if tracker.has_tip:
        origin = tracker.get_tip_origin()
        if origin is None:
          raise RuntimeError("No tip origin found.")
        tip_spots.append(origin)
        channels.append(channel)

    if len(tip_spots) == 0:
      raise RuntimeError("No tips have been picked up.")

    return await self.drop_tips(
      tip_spots=tip_spots,
      use_channels=channels,
      allow_nonzero_volume=allow_nonzero_volume,
      offsets=offsets,
      **backend_kwargs,
    )

  async def discard_tips(
    self,
    use_channels: Optional[List[int]] = None,
    allow_nonzero_volume: bool = True,
    offsets: Optional[List[Coordinate]] = None,
    **backend_kwargs,
  ):
    """Permanently discard tips in the trash.

    Examples:
      Discarding the tips on channels 1 and 2:

      >>> await lh.discard_tips(use_channels=[0, 1])

      Discarding all tips currently picked up:

      >>> await lh.discard_tips()

    Args:
      use_channels: List of channels to use. Index from front to back. If `None`, all that have
        tips will be used.
      allow_nonzero_volume: If `True`, tips will be returned even if their volumes are not zero.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    self._log_command(
      "discard_tips",
      use_channels=use_channels,
      allow_nonzero_volume=allow_nonzero_volume,
      offsets=offsets,
    )

    # Different default value from drop_tips: here we factor in the tip tracking.
    if use_channels is None:
      use_channels = [c for c, t in self.head.items() if t.has_tip]

    n = len(use_channels)

    if n == 0:
      raise RuntimeError("No tips have been picked up and no channels were specified.")

    trash = self.deck.get_trash_area()
    trash_offsets = get_tight_single_resource_liquid_op_offsets(
      trash,
      num_channels=n,
    )
    # add trash_offsets to offsets if defined, otherwise use trash_offsets
    # too advanced for mypy
    offsets = [
      o + to if o is not None else to
      for o, to in zip(offsets or [None] * n, trash_offsets)  # type: ignore
    ]

    return await self.drop_tips(
      tip_spots=[trash] * n,
      use_channels=use_channels,
      offsets=offsets,
      allow_nonzero_volume=allow_nonzero_volume,
      **backend_kwargs,
    )

  async def move_tips(
    self,
    source_tip_spots: List[TipSpot],
    dest_tip_spots: List[TipSpot],
  ):
    """Move tips from one tip rack to another.

    This is a convenience method that picks up tips from `source_tip_spots` and drops them to
    `dest_tip_spots`.

    Examples:
      Move tips from one tip rack to another:

      >>> await lh.move_tips(source_tip_rack["A1":"A8"], dest_tip_rack["B1":"B8"])
    """

    if len(source_tip_spots) != len(dest_tip_spots):
      raise ValueError("Number of source and destination tip spots must match.")

    use_channels = list(range(len(source_tip_spots)))

    await self.pick_up_tips(
      tip_spots=source_tip_spots,
      use_channels=use_channels,
    )
    await self.drop_tips(
      tip_spots=dest_tip_spots,
      use_channels=use_channels,
    )

  def _check_containers(self, resources: Sequence[Resource]):
    """Checks that all resources are containers."""
    not_containers = [r for r in resources if not isinstance(r, Container)]
    if len(not_containers) > 0:
      raise TypeError(f"Resources must be `Container`s, got {not_containers}")

  @need_setup_finished
  async def aspirate(
    self,
    resources: Sequence[Container],
    vols: List[float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Coordinate]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    spread: Literal["wide", "tight", "custom"] = "wide",
    mix: Optional[List[Mix]] = None,
    **backend_kwargs,
  ):
    """Aspirate liquid from the specified wells.

    Examples:
      Aspirate a constant amount of liquid from the first column:

      >>> await lh.aspirate(plate["A1:H1"], 50)

      Aspirate an linearly increasing amount of liquid from the first column:

      >>> await lh.aspirate(plate["A1:H1"], range(0, 500, 50))

      Aspirate arbitrary amounts of liquid from the first column:

      >>> await lh.aspirate(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Aspirate liquid from wells in different plates:

      >>> await lh.aspirate(plate["A1"] + plate2["A1"] + plate3["A1"], 50)

      Aspirating with a 10mm z-offset:

      >>> await lh.aspirate(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Aspirate from a blue bucket (big container), with the first 4 channels (which will be
      spaced equally apart):

      >>> await lh.aspirate(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      resources: A list of wells to aspirate liquid from. Can be a single resource, or a list of
        resources. If a single resource is specified, all channels will aspirate from the same
        resource.
      vols: A list of volumes to aspirate, one for each channel. If `vols` is a single number, then
        all channels will aspirate that volume.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(wells)` channels will be used.
      flow_rates: the aspiration speed. In ul/s. If `None`, the backend default will be used.
      offsets: List of offsets for each channel, a translation that will be applied to the
        aspiration location.
      liquid_height: The height of the liquid in the well wrt the bottom, in mm.
      blow_out_air_volume: The volume of air to aspirate after the liquid, in ul. If `None`, the
        backend default will be used.
      spread: Used if aspirating from a single resource with multiple channels. If "tight", the
        channels will be spaced as close as possible. If "wide", the channels will be spaced as far
        apart as possible. If "custom", the user must specify the offsets wrt the center of the
        resource.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If all channels are `None`.
    """

    self._log_command(
      "aspirate",
      resources=resources,
      vols=vols,
      use_channels=use_channels,
      flow_rates=flow_rates,
      offsets=offsets,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
    )

    self._check_containers(resources)

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))
    assert len(set(use_channels)) == len(use_channels), "Channels must be unique."

    # expand default arguments
    offsets = offsets or [Coordinate.zero()] * len(use_channels)
    flow_rates = flow_rates or [None] * len(use_channels)
    liquid_height = liquid_height or [None] * len(use_channels)
    blow_out_air_volume = blow_out_air_volume or [None] * len(use_channels)

    # Convert everything to floats to handle exotic number types
    vols = [float(v) for v in vols]
    flow_rates = [float(fr) if fr is not None else None for fr in flow_rates]
    liquid_height = [float(lh) if lh is not None else None for lh in liquid_height]
    blow_out_air_volume = [float(bav) if bav is not None else None for bav in blow_out_air_volume]

    self._blow_out_air_volume = blow_out_air_volume
    tips = [self.head[channel].get_tip() for channel in use_channels]

    # Checks
    for resource in resources:
      if isinstance(resource.parent, Plate) and resource.parent.has_lid():
        raise ValueError("Aspirating from a well with a lid is not supported.")

    self._make_sure_channels_exist(use_channels)
    for n, p in [
      ("resources", resources),
      ("vols", vols),
      ("offsets", offsets),
      ("flow_rates", flow_rates),
      ("liquid_height", liquid_height),
      ("blow_out_air_volume", blow_out_air_volume),
    ]:
      if len(p) != len(use_channels):
        raise ValueError(
          f"Length of {n} must match length of use_channels: {len(p)} != {len(use_channels)}"
        )

    # If the user specified a single resource, but multiple channels to use, we will assume they
    # want to space the channels evenly across the resource. Note that offsets are relative to the
    # center of the resource.
    if len(set(resources)) == 1:
      resource = resources[0]
      resources = [resource] * len(use_channels)
      if spread == "tight":
        center_offsets = get_tight_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )
      elif spread == "wide":
        center_offsets = get_wide_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )
      elif spread == "custom":
        center_offsets = [Coordinate.zero()] * len(use_channels)
      else:
        raise ValueError("Invalid value for 'spread'. Must be 'tight', 'wide', or 'custom'.")

      # add user defined offsets to the computed centers
      offsets = [c + o for c, o in zip(center_offsets, offsets)]

    # create operations
    aspirations = [
      SingleChannelAspiration(
        resource=r,
        volume=v,
        offset=o,
        flow_rate=fr,
        liquid_height=lh,
        tip=t,
        blow_out_air_volume=bav,
        mix=m,
      )
      for r, v, o, fr, lh, t, bav, m in zip(
        resources,
        vols,
        offsets,
        flow_rates,
        liquid_height,
        tips,
        blow_out_air_volume,
        mix or [None] * len(use_channels),  # type: ignore
      )
    ]

    # queue the operations on the resource (source) and mounted tips (destination) trackers
    for op in aspirations:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.remove_liquid(op.volume)
        op.tip.tracker.add_liquid(volume=op.volume)

    extras = self._check_args(
      self.backend.aspirate,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    # actually aspirate the liquid
    error: Optional[Exception] = None
    try:
      await self.backend.aspirate(ops=aspirations, use_channels=use_channels, **backend_kwargs)
    except Exception as e:
      error = e

    # determine which channels were successful
    successes = [error is None] * len(aspirations)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [channel_idx not in error.errors for channel_idx in use_channels]

    # commit or rollback the state trackers
    for channel, op, success in zip(use_channels, aspirations, successes):
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
        tip_volume_tracker = self.head[channel].get_tip().tracker
        (tip_volume_tracker.commit if success else tip_volume_tracker.rollback)()

    if error is not None:
      raise error

  @need_setup_finished
  async def dispense(
    self,
    resources: Sequence[Container],
    vols: List[float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Optional[List[Optional[float]]] = None,
    offsets: Optional[List[Coordinate]] = None,
    liquid_height: Optional[List[Optional[float]]] = None,
    blow_out_air_volume: Optional[List[Optional[float]]] = None,
    spread: Literal["wide", "tight", "custom"] = "wide",
    mix: Optional[List[Mix]] = None,
    **backend_kwargs,
  ):
    """Dispense liquid to the specified channels.

    Examples:
      Dispense a constant amount of liquid to the first column:

      >>> await lh.dispense(plate["A1:H1"], 50)

      Dispense an linearly increasing amount of liquid to the first column:

      >>> await lh.dispense(plate["A1:H1"], range(0, 500, 50))

      Dispense arbitrary amounts of liquid to the first column:

      >>> await lh.dispense(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Dispense liquid to wells in different plates:

      >>> await lh.dispense((plate["A1"], 50), (plate2["A1"], 50), (plate3["A1"], 50))

      Dispensing with a 10mm z-offset:

      >>> await lh.dispense(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Dispense a blue bucket (big container), with the first 4 channels (which will be spaced
      equally apart):

      >>> await lh.dispense(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      wells: A list of resources to dispense liquid to. Can be a list of resources, or a single
        resource, in which case all channels will dispense to that resource.
      vols: A list of volumes to dispense, one for each channel, or a single volume to dispense to
        all channels. If `vols` is a single number, then all channels will dispense that volume. In
        units of ul.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      flow_rates: the flow rates, in ul/s. If `None`, the backend default will be used.
      offsets: List of offsets for each channel, a translation that will be applied to the
        dispense location.
      liquid_height: The height of the liquid in the well wrt the bottom, in mm.
      blow_out_air_volume: The volume of air to dispense after the liquid, in ul. If `None`, the
        backend default will be used.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the dispense info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    self._log_command(
      "dispense",
      resources=resources,
      vols=vols,
      use_channels=use_channels,
      flow_rates=flow_rates,
      offsets=offsets,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
    )

    # If the user specified a single resource, but multiple channels to use, we will assume they
    # want to space the channels evenly across the resource. Note that offsets are relative to the
    # center of the resource.

    self._check_containers(resources)

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))
    assert len(set(use_channels)) == len(use_channels), "Channels must be unique."

    # expand default arguments
    offsets = offsets or [Coordinate.zero()] * len(use_channels)
    flow_rates = flow_rates or [None] * len(use_channels)
    liquid_height = liquid_height or [None] * len(use_channels)
    blow_out_air_volume = blow_out_air_volume or [None] * len(use_channels)

    # Convert everything to floats to handle exotic number types
    vols = [float(v) for v in vols]
    flow_rates = [float(fr) if fr is not None else None for fr in flow_rates]
    liquid_height = [float(lh) if lh is not None else None for lh in liquid_height]
    blow_out_air_volume = [float(bav) if bav is not None else None for bav in blow_out_air_volume]

    # If the user specified a single resource, but multiple channels to use, we will assume they
    # want to space the channels evenly across the resource. Note that offsets are relative to the
    # center of the resource.
    if len(set(resources)) == 1:
      resource = resources[0]
      resources = [resource] * len(use_channels)
      if spread == "tight":
        center_offsets = get_tight_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )
      elif spread == "wide":
        center_offsets = get_wide_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )
      elif spread == "custom":
        center_offsets = [Coordinate.zero()] * len(use_channels)
      else:
        raise ValueError("Invalid value for 'spread'. Must be 'tight', 'wide', or 'custom'.")

      # add user defined offsets to the computed centers
      offsets = [c + o for c, o in zip(center_offsets, offsets)]

    tips = [self.head[channel].get_tip() for channel in use_channels]

    # Check the blow out air volume with what was aspirated
    if does_volume_tracking():
      if any(bav is not None and bav != 0.0 for bav in blow_out_air_volume):
        if self._blow_out_air_volume is None:
          raise BlowOutVolumeError("No blowout volume was aspirated.")
        for requested_bav, done_bav in zip(blow_out_air_volume, self._blow_out_air_volume):
          if requested_bav is not None and done_bav is not None and requested_bav > done_bav:
            raise BlowOutVolumeError("Blowout volume is larger than aspirated volume")

    for resource in resources:
      if isinstance(resource.parent, Plate) and resource.parent.has_lid():
        raise ValueError("Dispensing to plate with lid")

    for n, p in [
      ("resources", resources),
      ("vols", vols),
      ("offsets", offsets),
      ("flow_rates", flow_rates),
      ("liquid_height", liquid_height),
      ("blow_out_air_volume", blow_out_air_volume),
    ]:
      if len(p) != len(use_channels):
        raise ValueError(
          f"Length of {n} must match length of use_channels: {len(p)} != {len(use_channels)}"
        )

    # create operations
    dispenses = [
      SingleChannelDispense(
        resource=r,
        volume=v,
        offset=o,
        flow_rate=fr,
        liquid_height=lh,
        tip=t,
        blow_out_air_volume=bav,
        mix=m,
      )
      for r, v, o, fr, lh, t, bav, m in zip(
        resources,
        vols,
        offsets,
        flow_rates,
        liquid_height,
        tips,
        blow_out_air_volume,
        mix or [None] * len(use_channels),  # type: ignore
      )
    ]

    # queue the operations on the resource (source) and mounted tips (destination) trackers
    for op in dispenses:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.add_liquid(volume=op.volume)
        op.tip.tracker.remove_liquid(op.volume)

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.dispense,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    # actually dispense the liquid
    error: Optional[Exception] = None
    try:
      await self.backend.dispense(ops=dispenses, use_channels=use_channels, **backend_kwargs)
    except Exception as e:
      error = e

    # determine which channels were successful
    successes = [error is None] * len(dispenses)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [channel_idx not in error.errors for channel_idx in use_channels]

    # commit or rollback the state trackers
    for channel, op, success in zip(use_channels, dispenses, successes):
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
        tip_volume_tracker = self.head[channel].get_tip().tracker
        (tip_volume_tracker.commit if success else tip_volume_tracker.rollback)()

    if any(bav is not None for bav in blow_out_air_volume):
      self._blow_out_air_volume = None

    if error is not None:
      raise error

  async def transfer(
    self,
    source: Well,
    targets: List[Well],
    source_vol: Optional[float] = None,
    ratios: Optional[List[float]] = None,
    target_vols: Optional[List[float]] = None,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rates: Optional[List[Optional[float]]] = None,
    **backend_kwargs,
  ):
    """Transfer liquid from one well to another.

    Examples:

      Transfer 50 uL of liquid from the first well to the second well:

      >>> await lh.transfer(plate["A1"], plate["B1"], source_vol=50)

      Transfer 80 uL of liquid from the first well equally to the first column:

      >>> await lh.transfer(plate["A1"], plate["A1:H1"], source_vol=80)

      Transfer 60 uL of liquid from the first well in a 1:2 ratio to 2 other wells:

      >>> await lh.transfer(plate["A1"], plate["B1:C1"], source_vol=60, ratios=[2, 1])

      Transfer arbitrary volumes to the first column:

      >>> await lh.transfer(plate["A1"], plate["A1:H1"], target_vols=[3, 1, 4, 1, 5, 9, 6, 2])

    Args:
      source: The source well.
      targets: The target wells.
      source_vol: The volume to transfer from the source well.
      ratios: The ratios to use when transferring liquid to the target wells. If not specified, then
        the volumes will be distributed equally.
      target_vols: The volumes to transfer to the target wells. If specified, `source_vols` and
        `ratios` must be `None`.
      aspiration_flow_rate: The flow rate to use when aspirating, in ul/s. If `None`, the backend
        default will be used.
      dispense_flow_rates: The flow rates to use when dispensing, in ul/s. If `None`, the backend
        default will be used. Either a single flow rate for all channels, or a list of flow rates,
        one for each target well.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.
    """

    self._log_command(
      "transfer",
      source=source,
      targets=targets,
      source_vol=source_vol,
      ratios=ratios,
      target_vols=target_vols,
      aspiration_flow_rate=aspiration_flow_rate,
      dispense_flow_rates=dispense_flow_rates,
    )

    if target_vols is not None:
      if ratios is not None:
        raise TypeError("Cannot specify ratios and target_vols at the same time")
      if source_vol is not None:
        raise TypeError("Cannot specify source_vol and target_vols at the same time")
    else:
      if source_vol is None:
        raise TypeError("Must specify either source_vol or target_vols")

      if ratios is None:
        ratios = [1] * len(targets)

      target_vols = [source_vol * r / sum(ratios) for r in ratios]

    await self.aspirate(
      resources=[source],
      vols=[sum(target_vols)],
      flow_rates=[aspiration_flow_rate],
      **backend_kwargs,
    )
    dispense_flow_rates = dispense_flow_rates or [None] * len(targets)
    for target, vol, dfr in zip(targets, target_vols, dispense_flow_rates):
      await self.dispense(
        resources=[target],
        vols=[vol],
        flow_rates=[dfr],
        use_channels=[0],
        **backend_kwargs,
      )

  @contextlib.contextmanager
  def use_channels(self, channels: List[int]):
    """Temporarily use the specified channels as a default argument to `use_channels`.

    Examples:
      Use channel index 2 for all liquid handling operations inside the context:

      >>> with lh.use_channels([2]):
      ...   await lh.pick_up_tips(tip_rack["A1"])
      ...   await lh.aspirate(plate["A1"], 50)
      ...   await lh.dispense(plate["A1"], 50)

      This is equivalent to:

      >>> await lh.pick_up_tips(tip_rack["A1"], use_channels=[2])
      >>> await lh.aspirate(plate["A1"], 50, use_channels=[2])
      >>> await lh.dispense(plate["A1"], 50, use_channels=[2])

      Within the context manager, you can override the default channels by specifying the
      `use_channels` argument explicitly.
    """

    self._default_use_channels = channels

    try:
      yield
    finally:
      self._default_use_channels = None

  @contextlib.asynccontextmanager
  async def use_tips(
    self,
    tip_spots: List[TipSpot],
    channels: Optional[List[int]] = None,
    discard: bool = True,
  ):
    """Temporarily pick up tips from the specified tip spots on the specified channels.

    This is a convenience method that picks up tips from `tip_spots` on `channels` when entering
    the context, and discards them when exiting the context. When passing `discard=False`, the tips
    will be returned instead of discarded.

    Examples:
      Use tips from A1 to H1 on channels 0 to 7, then discard:

      >>> with lh.use_tips(tip_rack["A1":"H1"], channels=list(range(8))):
      ...   await lh.aspirate(plate["A1":"H1"], vols=[50]*8)
      ...   await lh.dispense(plate["A1":"H1"], vols=[50]*8)

      This is equivalent to:

      >>> await lh.pick_up_tips(tip_rack["A1":"H1"], use_channels=list(range(8)))
      >>> await lh.aspirate(plate["A1":"H1"], vols=[50]*8, use_channels=list(range(8)))
      >>> await lh.dispense(plate["A1":"H1"], vols=[50]*8, use_channels=list(range(8)))
      >>> await lh.discard_tips(use_channels=list(range(8)))

      Use tips from A1 to H1 on channels 0 to 7, but return them instead of discarding:

      >>> with lh.use_tips(tip_rack["A1":"H1"], channels=list(range(8)), discard=False):
      ...   await lh.aspirate(plate["A1":"H1"], vols=[50]*8)
      ...   await lh.dispense(plate["A1":"H1"], vols=[50]*8)

      This is equivalent to:

      >>> await lh.pick_up_tips(tip_rack["A1":"H1"], use_channels=list(range(8)))
      >>> await lh.aspirate(plate["A1":"H1"], vols=[50]*8, use_channels=list(range(8)))
      >>> await lh.dispense(plate["A1":"H1"], vols=[50]*8, use_channels=list(range(8)))
      >>> await lh.return_tips(use_channels=list(range(8)))
    """

    if channels is None:
      channels = list(range(len(tip_spots)))

    if len(tip_spots) != len(channels):
      raise ValueError("Number of tip spots and channels must match.")

    await self.pick_up_tips(tip_spots, use_channels=channels)
    try:
      yield
    finally:
      if discard:
        await self.discard_tips(use_channels=channels)
      else:
        await self.return_tips(use_channels=channels)

  async def pick_up_tips96(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    **backend_kwargs,
  ):
    """Pick up tips using the 96 head. This will pick up 96 tips.

    Examples:
      Pick up tips from a 96-tip tiprack:

      >>> await lh.pick_up_tips96(my_tiprack)

    Args:
      tip_rack: The tip rack to pick up tips from.
      offset: Additional offset to use when picking up tips. This is added to
        :attr:`default_offset_head96`.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    offset = self.default_offset_head96 + offset

    self._log_command(
      "pick_up_tips96",
      tip_rack=tip_rack,
      offset=offset,
    )

    if not isinstance(tip_rack, TipRack):
      raise TypeError(f"Resource must be a TipRack, got {tip_rack}")
    if not tip_rack.num_items == 96:
      raise ValueError("Tip rack must have 96 tips")

    extras = self._check_args(
      self.backend.pick_up_tips96, backend_kwargs, default={"pickup"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    # queue operation on all tip trackers
    tips: List[Optional[Tip]] = []
    for i, tip_spot in enumerate(tip_rack.get_all_items()):
      if not does_tip_tracking() and self.head96[i].has_tip:
        self.head96[i].remove_tip()
      # only add tips where there is one present.
      # it's possible only some tips are present in the tip rack.
      if tip_spot.has_tip():
        self.head96[i].add_tip(tip_spot.get_tip(), origin=tip_spot, commit=False)
        tips.append(tip_spot.get_tip())
      else:
        tips.append(None)
      if does_tip_tracking() and not tip_spot.tracker.is_disabled and tip_spot.has_tip():
        tip_spot.tracker.remove_tip()

    pickup_operation = PickupTipRack(resource=tip_rack, offset=offset, tips=tips)
    try:
      await self.backend.pick_up_tips96(pickup=pickup_operation, **backend_kwargs)
    except Exception as error:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.rollback()
        self.head96[i].rollback()
      raise error
    else:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.commit()
        self.head96[i].commit()

  async def drop_tips96(
    self,
    resource: Union[TipRack, Trash],
    offset: Coordinate = Coordinate.zero(),
    allow_nonzero_volume: bool = False,
    **backend_kwargs,
  ):
    """Drop tips using the 96 head. This will drop 96 tips.

    Examples:
      Drop tips to a 96-tip tiprack:

      >>> await lh.drop_tips96(my_tiprack)

      Drop tips to the trash:

      >>> await lh.drop_tips96(lh.deck.get_trash_area96())

    Args:
      resource: The tip rack to drop tips to.
      offset: Additional offset to use when dropping tips. This is added to
        :attr:`default_offset_head96`.
      allow_nonzero_volume: If `True`, the tip will be dropped even if its volume is not zero (there
        is liquid in the tip). If `False`, a RuntimeError will be raised if the tip has nonzero
        volume.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    offset = self.default_offset_head96 + offset

    self._log_command(
      "drop_tips96",
      resource=resource,
      offset=offset,
      allow_nonzero_volume=allow_nonzero_volume,
    )

    if not isinstance(resource, (TipRack, Trash)):
      raise TypeError(f"Resource must be a TipRack or Trash, got {resource}")
    if isinstance(resource, TipRack) and not resource.num_items == 96:
      raise ValueError("Tip rack must have 96 tips")

    extras = self._check_args(
      self.backend.drop_tips96, backend_kwargs, default={"drop"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    # queue operation on all tip trackers
    for i in range(96):
      # it's possible not every channel on this head has a tip.
      if not self.head96[i].has_tip:
        continue
      tip = self.head96[i].get_tip()
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume and does_volume_tracking():
        error = f"Cannot drop tip with volume {tip.tracker.get_used_volume()} on channel {i}"
        raise RuntimeError(error)
      if isinstance(resource, TipRack):
        tip_spot = resource.get_item(i)
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.add_tip(tip, commit=False)
      self.head96[i].remove_tip()

    drop_operation = DropTipRack(resource=resource, offset=offset)
    try:
      await self.backend.drop_tips96(drop=drop_operation, **backend_kwargs)
    except Exception as e:
      for i in range(96):
        if isinstance(resource, TipRack):
          tip_spot = resource.get_item(i)
          if does_tip_tracking() and not tip_spot.tracker.is_disabled:
            tip_spot.tracker.rollback()
        self.head96[i].rollback()
      raise e
    else:
      for i in range(96):
        if isinstance(resource, TipRack):
          tip_spot = resource.get_item(i)
          if does_tip_tracking() and not tip_spot.tracker.is_disabled:
            tip_spot.tracker.commit()
        self.head96[i].commit()

  def _get_96_head_origin_tip_rack(self) -> Optional[TipRack]:
    """Get the tip rack where the tips on the 96 head were picked up. If no tips were picked up,
    return `None`. If different tip racks were found for different tips on the head, raise a
    RuntimeError."""

    tip_spot = self.head96[0].get_tip_origin()
    if tip_spot is None:
      return None
    tip_rack = tip_spot.parent
    if tip_rack is None:
      # very unlikely, but just in case
      raise RuntimeError("No tip rack found for tip")
    for i in range(tip_rack.num_items):
      other_tip_spot = self.head96[i].get_tip_origin()
      if other_tip_spot is None:
        raise RuntimeError("Not all channels have a tip origin")
      other_tip_rack = other_tip_spot.parent
      if tip_rack != other_tip_rack:
        raise RuntimeError("All tips must be from the same tip rack")
    return tip_rack

  async def return_tips96(
    self,
    allow_nonzero_volume: bool = False,
    offset: Coordinate = Coordinate.zero(),
    **backend_kwargs,
  ):
    """Return the tips on the 96 head to the tip rack where they were picked up.

    Examples:
      Return the tips on the 96 head to the tip rack where they were picked up:

      >>> await lh.pick_up_tips96(my_tiprack)
      >>> await lh.return_tips96()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    self._log_command(
      "return_tips96",
      allow_nonzero_volume=allow_nonzero_volume,
    )

    tip_rack = self._get_96_head_origin_tip_rack()
    if tip_rack is None:
      raise RuntimeError("No tips have been picked up with the 96 head")
    return await self.drop_tips96(
      tip_rack,
      allow_nonzero_volume=allow_nonzero_volume,
      offset=offset,
      **backend_kwargs,
    )

  async def discard_tips96(self, allow_nonzero_volume: bool = True, **backend_kwargs):
    """Permanently discard tips from the 96 head in the trash. This method only works when this
    LiquidHandler is configured with a deck that implements the `get_trash_area96` method.
    Otherwise, an `ImplementationError` will be raised.

    Examples:
      Discard the tips on the 96 head:

      >>> await lh.discard_tips96()

    Args:
      allow_nonzero_volume: If `True`, the tip will be dropped even if its volume is not zero (there
        is liquid in the tip). If `False`, a RuntimeError will be raised if the tip has nonzero
        volume.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      ImplementationError: If the deck does not implement the `get_trash_area96` method.
    """

    self._log_command(
      "discard_tips96",
      allow_nonzero_volume=allow_nonzero_volume,
    )

    return await self.drop_tips96(
      self.deck.get_trash_area96(),
      allow_nonzero_volume=allow_nonzero_volume,
      **backend_kwargs,
    )

  def _check_96_head_fits_in_container(self, container: Container) -> bool:
    """Check if the 96 head can fit in the given container."""

    tip_width = 2  # approximation
    distance_between_tips = 9

    return (
      container.get_absolute_size_x() >= tip_width + distance_between_tips * 11
      and container.get_absolute_size_y() >= tip_width + distance_between_tips * 7
    )

  async def aspirate96(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    liquid_height: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    mix: Optional[Mix] = None,
    **backend_kwargs,
  ):
    """Aspirate from all wells in a plate or from a container of a sufficient size.

    Examples:
      Aspirate an entire 96 well plate or a container of sufficient size:

      >>> await lh.aspirate96(plate, volume=50)
      >>> await lh.aspirate96(container, volume=50)

    Args:
      resource: Resource object or list of wells.
      volume: The volume to aspirate through each channel
      offset: Adjustment to where the 96 head should go to aspirate relative to where the plate or container is defined to be. Added to :attr:`default_offset_head96`.  Defaults to :func:`Coordinate.zero`.
      flow_rate: The flow rate to use when aspirating, in ul/s. If `None`, the
        backend default will be used.
      liquid_height: The height of the liquid in the well wrt the bottom, in mm. If `None`, the backend default will be used.
      blow_out_air_volume: The volume of air to aspirate after the liquid, in ul. If `None`, the backend default will be used.
      mix: A mix operation to perform after the aspiration, optional.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    offset = self.default_offset_head96 + offset

    self._log_command(
      "aspirate96",
      resource=resource,
      volume=volume,
      offset=offset,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=mix,
    )

    if not (
      isinstance(resource, (Plate, Container))
      or (isinstance(resource, list) and all(isinstance(w, Well) for w in resource))
    ):
      raise TypeError(f"Resource must be a Plate, Container, or list of Wells, got {resource}")

    extras = self._check_args(
      self.backend.aspirate96, backend_kwargs, default={"aspiration"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    tips = [channel.get_tip() if channel.has_tip else None for channel in self.head96.values()]
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]

    # Convert everything to floats to handle exotic number types
    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    # Convert Plate to either one Container (single well) or a list of Wells
    containers: Sequence[Container]
    if isinstance(resource, Plate):
      if resource.has_lid():
        raise ValueError("Aspirating from plate with lid")
      containers = resource.get_all_items() if resource.num_items > 1 else [resource.get_item(0)]
    elif isinstance(resource, Container):
      containers = [resource]
    elif isinstance(resource, list) and all(isinstance(w, Well) for w in resource):
      containers = resource
    else:
      raise TypeError(
        f"Resource must be a Plate, Container, or list of Wells, got {type(resource)} "
        f" for {resource}"
      )

    if len(containers) == 1:  # single container
      container = containers[0]
      if not self._check_96_head_fits_in_container(container):
        raise ValueError("Container too small to accommodate 96 head")

      for tip in tips:
        if tip is None:
          continue

        if not container.tracker.is_disabled and does_volume_tracking():
          container.tracker.remove_liquid(volume=volume)
        tip.tracker.add_liquid(volume=volume)

      aspiration = MultiHeadAspirationContainer(
        container=container,
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=liquid_height,
        blow_out_air_volume=blow_out_air_volume,
        mix=mix,
      )
    else:  # multiple containers
      # ensure that wells are all in the same plate
      plate = containers[0].parent
      for well in containers:
        if well.parent != plate:
          raise ValueError("All wells must be in the same plate")

      if not len(containers) == 96:
        raise ValueError(f"aspirate96 expects 96 containers when a list, got {len(containers)}")

      for well, tip in zip(containers, tips):
        if tip is None:
          continue

        if not well.tracker.is_disabled and does_volume_tracking():
          well.tracker.remove_liquid(volume=volume)
        tip.tracker.add_liquid(volume=volume)

      aspiration = MultiHeadAspirationPlate(
        wells=cast(List[Well], containers),
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=liquid_height,
        blow_out_air_volume=blow_out_air_volume,
        mix=mix,
      )

    try:
      await self.backend.aspirate96(aspiration=aspiration, **backend_kwargs)
    except Exception:
      for tip in tips:
        if tip is not None:
          tip.tracker.rollback()
      for container in containers:
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.rollback()
      raise
    else:
      for tip in tips:
        if tip is not None:
          tip.tracker.commit()
      for container in containers:
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.commit()

  async def dispense96(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    liquid_height: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    mix: Optional[Mix] = None,
    **backend_kwargs,
  ):
    """Dispense to all wells in a plate.

    Examples:
      Dispense an entire 96 well plate:

      >>> await lh.dispense96(plate, volume=50)

    Args:
      resource: Resource object or list of wells.
      volume: The volume to dispense through each channel
      offset: Adjustment to where the 96 head should go to aspirate relative to where the plate or container is defined to be. Added to :attr:`default_offset_head96`.  Defaults to :func:`Coordinate.zero`.
      flow_rate: The flow rate to use when dispensing, in ul/s. If `None`, the backend default will be used.
      liquid_height: The height of the liquid in the well wrt the bottom, in mm. If `None`, the backend default will be used.
      blow_out_air_volume: The volume of air to dispense after the liquid, in ul. If `None`, the backend default will be used.
      mix: If provided, the tip will mix after dispensing.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    offset = self.default_offset_head96 + offset

    self._log_command(
      "dispense96",
      resource=resource,
      volume=volume,
      offset=offset,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=mix,
    )

    if not (
      isinstance(resource, (Plate, Container))
      or (isinstance(resource, list) and all(isinstance(w, Well) for w in resource))
    ):
      raise TypeError(f"Resource must be a Plate, Container, or list of Wells, got {resource}")

    extras = self._check_args(
      self.backend.dispense96, backend_kwargs, default={"dispense"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    tips = [channel.get_tip() if channel.has_tip else None for channel in self.head96.values()]
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]

    # Convert everything to floats to handle exotic number types
    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    # Convert Plate to either one Container (single well) or a list of Wells
    containers: Sequence[Container]
    if isinstance(resource, Plate):
      if resource.has_lid():
        raise ValueError("Dispensing to plate with lid is not possible. Remove the lid first.")
      containers = resource.get_all_items() if resource.num_items > 1 else [resource.get_item(0)]
    elif isinstance(resource, Container):
      containers = [resource]
    elif isinstance(resource, list) and all(isinstance(w, Well) for w in resource):
      containers = resource
    else:
      raise TypeError(
        f"Resource must be a Plate, Container, or list of Wells, got {type(resource)} "
        f"for {resource}"
      )

    # if we have enough liquid in the tip, remove it from the tip tracker for accounting.
    # if we do not (for example because the plunger was up on tip pickup), and we
    # do not have volume tracking enabled, we just ignore it.
    for tip in tips:
      if tip is None:
        continue

      if does_volume_tracking():
        tip.tracker.remove_liquid(volume=volume)
      elif tip.tracker.get_used_volume() < volume:
        tip.tracker.remove_liquid(volume=min(tip.tracker.get_used_volume(), volume))

    if len(containers) == 1:  # single container
      container = containers[0]
      if not self._check_96_head_fits_in_container(container):
        raise ValueError("Container too small to accommodate 96 head")

      if not container.tracker.is_disabled and does_volume_tracking():
        container.tracker.add_liquid(volume=len([t for t in tips if t is not None]) * volume)

      dispense = MultiHeadDispenseContainer(
        container=container,
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=liquid_height,
        blow_out_air_volume=blow_out_air_volume,
        mix=mix,
      )
    else:
      # ensure that wells are all in the same plate
      plate = containers[0].parent
      for well in containers:
        if well.parent != plate:
          raise ValueError("All wells must be in the same plate")

      if not len(containers) == 96:
        raise ValueError(f"dispense96 expects 96 wells, got {len(containers)}")

      for well, tip in zip(containers, tips):
        if tip is None:
          continue

        if not well.tracker.is_disabled and does_volume_tracking():
          well.tracker.add_liquid(volume=volume)

      dispense = MultiHeadDispensePlate(
        wells=cast(List[Well], containers),
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=liquid_height,
        blow_out_air_volume=blow_out_air_volume,
        mix=mix,
      )

    try:
      await self.backend.dispense96(dispense=dispense, **backend_kwargs)
    except Exception:
      for tip in tips:
        if tip is not None:
          tip.tracker.rollback()
      for container in containers:
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.rollback()
      raise
    else:
      for tip in tips:
        if tip is not None:
          tip.tracker.commit()
      for container in containers:
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.commit()

  async def stamp(
    self,
    source: Plate,  # TODO
    target: Plate,
    volume: float,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rate: Optional[float] = None,
  ):
    """Stamp (aspiration and dispense) one plate onto another.

    Args:
      source: the source plate
      target: the target plate
      volume: the volume to be transported
      aspiration_flow_rate: the flow rate for the aspiration, in ul/s. If `None`, the backend
        default will be used.
      dispense_flow_rate: the flow rate for the dispense, in ul/s. If `None`, the backend default
        will be used.
    """

    self._log_command(
      "stamp",
      source=source,
      target=target,
      volume=volume,
      aspiration_flow_rate=aspiration_flow_rate,
      dispense_flow_rate=dispense_flow_rate,
    )

    assert (source.num_items_x, source.num_items_y) == (
      target.num_items_x,
      target.num_items_y,
    ), "Source and target plates must be the same shape"

    await self.aspirate96(resource=source, volume=volume, flow_rate=aspiration_flow_rate)
    await self.dispense96(resource=source, volume=volume, flow_rate=dispense_flow_rate)

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    direction: GripDirection = GripDirection.FRONT,
    **backend_kwargs,
  ):
    self._log_command(
      "pick_up_resource",
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      direction=direction,
    )

    if self._resource_pickup is not None:
      raise RuntimeError(f"Resource {self._resource_pickup.resource.name} already picked up")

    self._resource_pickup = ResourcePickup(
      resource=resource,
      offset=offset,
      pickup_distance_from_top=pickup_distance_from_top,
      direction=direction,
    )

    extras = self._check_args(
      self.backend.pick_up_resource, backend_kwargs, default={"pickup"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    try:
      await self.backend.pick_up_resource(
        pickup=self._resource_pickup,
        **backend_kwargs,
      )
    except Exception as e:
      self._resource_pickup = None
      raise e

  async def move_picked_up_resource(
    self,
    to: Coordinate,
    offset: Coordinate = Coordinate.zero(),
    direction: Optional[GripDirection] = None,
    **backend_kwargs,
  ):
    """Move a resource that has been picked up to a new location.

    Args:
      to: The new location to move the resource to. (LFB of plate)
      offset: The offset to apply to the new location.
      direction: The direction in which the resource is gripped. If `None`, the current direction
        will be used.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    self._log_command(
      "move_picked_up_resource",
      to=to,
      offset=offset,
    )

    if self._resource_pickup is None:
      raise RuntimeError("No resource picked up")
    await self.backend.move_picked_up_resource(
      ResourceMove(
        location=to,
        resource=self._resource_pickup.resource,
        gripped_direction=direction or self._resource_pickup.direction,
        pickup_distance_from_top=self._resource_pickup.pickup_distance_from_top,
        offset=offset,
      ),
      **backend_kwargs,
    )

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    direction: GripDirection = GripDirection.FRONT,
    **backend_kwargs,
  ):
    self._log_command(
      "drop_resource",
      destination=destination,
      offset=offset,
      direction=direction,
    )

    if self._resource_pickup is None:
      raise RuntimeError("No resource picked up")
    resource = self._resource_pickup.resource

    # compute rotation based on the pickup_direction and drop_direction
    if self._resource_pickup.direction == direction:
      rotation_applied_by_move = 0
    if (self._resource_pickup.direction, direction) in (
      (GripDirection.FRONT, GripDirection.RIGHT),
      (GripDirection.RIGHT, GripDirection.BACK),
      (GripDirection.BACK, GripDirection.LEFT),
      (GripDirection.LEFT, GripDirection.FRONT),
    ):
      rotation_applied_by_move = 90
    if (self._resource_pickup.direction, direction) in (
      (GripDirection.FRONT, GripDirection.BACK),
      (GripDirection.BACK, GripDirection.FRONT),
      (GripDirection.LEFT, GripDirection.RIGHT),
      (GripDirection.RIGHT, GripDirection.LEFT),
    ):
      rotation_applied_by_move = 180
    if (self._resource_pickup.direction, direction) in (
      (GripDirection.RIGHT, GripDirection.FRONT),
      (GripDirection.BACK, GripDirection.RIGHT),
      (GripDirection.LEFT, GripDirection.BACK),
      (GripDirection.FRONT, GripDirection.LEFT),
    ):
      rotation_applied_by_move = 270

    # the resource's absolute rotation should be the resource's previous rotation plus the
    # rotation the move applied. The resource's absolute rotation is the rotation of the
    # new parent plus the resource's rotation relative to the parent. So to find the new
    # rotation of the resource wrt its new parent, we compute what the new absolute rotation
    # should be and subtract the rotation of the new parent.

    # moving from a resource from a rotated parent to a non-rotated parent means child inherits/'houses' the rotation after move
    resource_absolute_rotation_after_move = (
      resource.get_absolute_rotation().z + rotation_applied_by_move
    )
    destination_rotation = (
      destination.get_absolute_rotation().z if not isinstance(destination, Coordinate) else 0
    )
    resource_rotation_wrt_destination = resource_absolute_rotation_after_move - destination_rotation

    # `get_default_child_location`, which is used to compute the translation of the child wrt the parent,
    # only considers the child's local rotation. In order to set this new child rotation locally for the
    # translation computation, we have to subtract the current rotation of the resource, so we can use
    # resource.rotated(z=resource_rotation_wrt_destination_wrt_local) to 'set' the new local rotation.
    # Remember, rotated() applies the rotation on top of the current rotation. <- TODO: stupid
    resource_rotation_wrt_destination_wrt_local = (
      resource_rotation_wrt_destination - resource.rotation.z
    )

    # get the location of the destination
    if isinstance(destination, ResourceStack):
      assert (
        destination.direction == "z"
      ), "Only ResourceStacks with direction 'z' are currently supported"

      # the resource can be rotated wrt the ResourceStack. This is allowed as long
      # as it's in multiples of 180 degrees. 90 degrees is not allowed.
      if resource_rotation_wrt_destination % 180 != 0:
        raise ValueError(
          "Resource rotation wrt ResourceStack must be a multiple of 180 degrees, "
          f"got {resource_rotation_wrt_destination} degrees"
        )

      to_location = destination.get_location_wrt(self.deck) + destination.get_new_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
    elif isinstance(destination, Coordinate):
      to_location = destination
    elif isinstance(destination, ResourceHolder):
      if destination.resource is not None and destination.resource is not resource:
        raise RuntimeError("Destination already has a plate")
      child_wrt_parent = destination.get_default_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      to_location = destination.get_location_wrt(self.deck) + child_wrt_parent
    elif isinstance(destination, PlateAdapter):
      if not isinstance(resource, Plate):
        raise ValueError("Only plates can be moved to a PlateAdapter")
      # Calculate location adjustment of Plate based on PlateAdapter geometry
      adjusted_plate_anchor = destination.compute_plate_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      to_location = destination.get_location_wrt(self.deck) + adjusted_plate_anchor
    elif isinstance(destination, Plate) and isinstance(resource, Lid):
      lid = resource
      plate_location = destination.get_location_wrt(self.deck)
      child_wrt_parent = destination.get_lid_location(
        lid.rotated(z=resource_rotation_wrt_destination_wrt_local)
      ).rotated(destination.get_absolute_rotation())
      to_location = plate_location + child_wrt_parent
    else:
      to_location = destination.get_location_wrt(self.deck)

    drop = ResourceDrop(
      resource=self._resource_pickup.resource,
      destination=to_location,
      destination_absolute_rotation=destination.get_absolute_rotation()
      if isinstance(destination, Resource)
      else Rotation(0, 0, 0),
      offset=offset,
      pickup_distance_from_top=self._resource_pickup.pickup_distance_from_top,
      pickup_direction=self._resource_pickup.direction,
      direction=direction,
      rotation=rotation_applied_by_move,
    )
    result = await self.backend.drop_resource(drop=drop, **backend_kwargs)

    self._resource_pickup = None

    # we rotate the resource on top of its original rotation. So in order to set the new rotation,
    # we have to subtract its current rotation.
    resource.rotate(z=resource_rotation_wrt_destination - resource.rotation.z)

    # assign to destination
    resource.unassign()
    if isinstance(destination, Coordinate):
      to_location -= self.deck.location  # passed as an absolute location, but stored as relative
      self.deck.assign_child_resource(resource, location=to_location)
    elif isinstance(destination, PlateHolder):  # .zero() resources
      destination.assign_child_resource(resource)
    elif isinstance(destination, ResourceHolder):  # .zero() resources
      destination.assign_child_resource(resource)
    elif isinstance(destination, (ResourceStack, PlateReader)):  # manage its own resources
      if isinstance(destination, ResourceStack) and destination.direction != "z":
        raise ValueError("Only ResourceStacks with direction 'z' are currently supported")
      destination.assign_child_resource(resource)
    elif isinstance(destination, Tilter):
      destination.assign_child_resource(resource, location=destination.child_location)
    elif isinstance(destination, PlateAdapter):
      if not isinstance(resource, Plate):
        raise ValueError("Only plates can be moved to a PlateAdapter")
      destination.assign_child_resource(
        resource, location=destination.compute_plate_location(resource)
      )
    elif isinstance(destination, Plate) and isinstance(resource, Lid):
      destination.assign_child_resource(resource)
    elif isinstance(destination, Trash):
      pass  # don't assign to trash, resource will simply be unassigned
    else:
      destination.assign_child_resource(resource, location=to_location)

    return result

  async def move_resource(
    self,
    resource: Resource,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    pickup_direction: GripDirection = GripDirection.FRONT,
    drop_direction: GripDirection = GripDirection.FRONT,
    **backend_kwargs,
  ):
    """Move a resource to a new location.

    Has convenience methods :meth:`move_plate` and :meth:`move_lid`.

    Examples:
      Move a plate to a new location:

      >>> await lh.move_resource(plate, to=Coordinate(100, 100, 100))

    Args:
      resource: The Resource object.
      to: The absolute coordinate (meaning relative to deck) to move the resource to.
      intermediate_locations: A list of intermediate locations to move the resource through.
      pickup_offset: The offset from the resource's origin, optional (rarely necessary).
      destination_offset: The offset from the location's origin, optional (rarely necessary).
      pickup_distance_from_top: The distance from the top of the resource to pick up from.
      pickup_direction: The direction from which to pick up the resource.
      drop_direction: The direction from which to put down the resource.
    """

    self._log_command(
      "move_resource",
      resource=resource,
      to=to,
      intermediate_locations=intermediate_locations,
      pickup_offset=pickup_offset,
      destination_offset=destination_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      pickup_direction=pickup_direction,
      drop_direction=drop_direction,
    )

    extra = self._check_args(
      self.backend.pick_up_resource,
      backend_kwargs,
      default={"pickup"},
      strictness=Strictness.IGNORE,
    )
    pickup_kwargs = {k: v for k, v in backend_kwargs.items() if k not in extra}

    await self.pick_up_resource(
      resource=resource,
      offset=pickup_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      direction=pickup_direction,
      **pickup_kwargs,
    )

    for intermediate_location in intermediate_locations or []:
      await self.move_picked_up_resource(to=intermediate_location)

    extra = self._check_args(
      self.backend.drop_resource,
      backend_kwargs,
      default={"drop"},
      strictness=Strictness.IGNORE,
    )
    drop_kwargs = {k: v for k, v in backend_kwargs.items() if k not in extra}

    await self.drop_resource(
      destination=to,
      offset=destination_offset,
      direction=drop_direction,
      **drop_kwargs,
    )

  async def move_lid(
    self,
    lid: Lid,
    to: Union[Plate, ResourceStack, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_direction: GripDirection = GripDirection.FRONT,
    drop_direction: GripDirection = GripDirection.FRONT,
    pickup_distance_from_top: float = 5.7 - 3.33,
    **backend_kwargs,
  ):
    """Move a lid to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a lid to the :class:`~resources.ResourceStack`:

      >>> await lh.move_lid(plate.lid, stacking_area)

      Move a lid to the stacking area and back, grabbing it from the left side:

      >>> await lh.move_lid(plate.lid, stacking_area, pickup_direction=GripDirection.LEFT)
      >>> await lh.move_lid(stacking_area.get_top_item(), plate, drop_direction=GripDirection.LEFT)

    Args:
      lid: The lid to move. Can be either a Plate object or a Lid object.
      to: The location to move the lid to, either a plate, ResourceStack or a Coordinate.
      pickup_offset: The offset from the resource's origin, optional (rarely necessary).
      destination_offset: The offset from the location's origin, optional (rarely necessary).

    Raises:
      ValueError: If the lid is not assigned to a resource.
    """

    self._log_command(
      "move_lid",
      lid=lid,
      to=to,
      intermediate_locations=intermediate_locations,
      pickup_offset=pickup_offset,
      destination_offset=destination_offset,
      pickup_direction=pickup_direction,
      drop_direction=drop_direction,
      pickup_distance_from_top=pickup_distance_from_top,
    )

    await self.move_resource(
      lid,
      to=to,
      intermediate_locations=intermediate_locations,
      pickup_distance_from_top=pickup_distance_from_top,
      pickup_offset=pickup_offset,
      destination_offset=destination_offset,
      pickup_direction=pickup_direction,
      drop_direction=drop_direction,
      **backend_kwargs,
    )

  async def move_plate(
    self,
    plate: Plate,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    drop_direction: GripDirection = GripDirection.FRONT,
    pickup_direction: GripDirection = GripDirection.FRONT,
    pickup_distance_from_top: float = 13.2 - 3.33,
    **backend_kwargs,
  ):
    """Move a plate to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a plate to into a carrier spot:

      >>> await lh.move_plate(plate, plt_car[1])

      Move a plate to an absolute location:

      >>> await lh.move_plate(plate_01, Coordinate(100, 100, 100))

      Move a lid to another carrier spot, grabbing it from the left side:

      >>> await lh.move_plate(plate, plt_car[1], pickup_direction=GripDirection.LEFT)
      >>> await lh.move_plate(plate, plt_car[0], drop_direction=GripDirection.LEFT)

      Move a resource while visiting a few intermediate locations along the way:

      >>> await lh.move_plate(plate, plt_car[1], intermediate_locations=[
      ...   Coordinate(100, 100, 100),
      ...   Coordinate(200, 200, 200),
      ... ])

    Args:
      plate: The plate to move. Can be either a Plate object or a ResourceHolder object.
      to: The location to move the plate to, either a plate, ResourceHolder or a Coordinate.
      pickup_offset: The offset from the resource's origin, optional (rarely necessary).
      destination_offset: The offset from the location's origin, optional (rarely necessary).
    """

    self._log_command(
      "move_plate",
      plate=plate,
      to=to,
      intermediate_locations=intermediate_locations,
      pickup_offset=pickup_offset,
      destination_offset=destination_offset,
      pickup_direction=pickup_direction,
      drop_direction=drop_direction,
      pickup_distance_from_top=pickup_distance_from_top,
    )

    await self.move_resource(
      plate,
      to=to,
      intermediate_locations=intermediate_locations,
      pickup_distance_from_top=pickup_distance_from_top,
      pickup_offset=pickup_offset,
      destination_offset=destination_offset,
      pickup_direction=pickup_direction,
      drop_direction=drop_direction,
      **backend_kwargs,
    )

  def serialize(self):
    return {
      **Resource.serialize(self),
      **Machine.serialize(self),
      "default_offset_head96": serialize(self.default_offset_head96),
    }

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> LiquidHandler:
    """Deserialize a liquid handler from a dictionary.

    Args:
      data: A dictionary representation of the liquid handler.
    """

    deck_data = data["children"][0]
    deck = Deck.deserialize(data=deck_data, allow_marshal=allow_marshal)
    backend = LiquidHandlerBackend.deserialize(data=data["backend"])

    if "default_offset_head96" in data:
      default_offset = deserialize(data["default_offset_head96"], allow_marshal=allow_marshal)
      assert isinstance(default_offset, Coordinate)
    else:
      default_offset = Coordinate.zero()

    return cls(
      deck=deck,
      backend=backend,
      default_offset_head96=default_offset,
    )

  @classmethod
  def load(cls, path: str) -> LiquidHandler:
    """Load a liquid handler from a file.

    Args:
      path: The path to the file to load from.
    """

    with open(path, "r", encoding="utf-8") as f:
      return cls.deserialize(json.load(f))

  async def prepare_for_manual_channel_operation(self, channel: int):
    self._log_command(
      "prepare_for_manual_channel_operation",
      channel=channel,
    )

    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.prepare_for_manual_channel_operation(channel=channel)

  async def move_channel_x(self, channel: int, x: float):
    """Move channel to absolute x position"""
    self._log_command("move_channel_x", channel=channel, x=x)
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.move_channel_x(channel=channel, x=x)

  async def move_channel_y(self, channel: int, y: float):
    """Move channel to absolute y position"""
    self._log_command("move_channel_y", channel=channel, y=y)
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.move_channel_y(channel=channel, y=y)

  async def move_channel_z(self, channel: int, z: float):
    """Move channel to absolute z position"""
    self._log_command("move_channel_z", channel=channel, z=z)
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.move_channel_z(channel=channel, z=z)

  # -- Resource methods --

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True,
  ):
    """Not implement on LiquidHandler, since the deck is managed by the :attr:`deck` attribute."""
    raise NotImplementedError(
      "Cannot assign child resource to liquid handler. Use lh.deck.assign_child_resource() instead."
    )

  async def probe_tip_presence_via_pickup(
    self, tip_spots: List[TipSpot], use_channels: Optional[List[int]] = None
  ) -> Dict[str, bool]:
    """Probe tip presence by attempting pickup on each TipSpot.

    Args:
      tip_spots: TipSpots to probe.
      use_channels: Channels to use (must match tip_spots length).

    Returns:
      Dict[str, bool]: Mapping of tip spot names to presence flags.
    """

    if use_channels is None:
      use_channels = list(range(len(tip_spots)))

    if len(use_channels) > self.backend.num_channels:
      raise ValueError(
        "Liquid handler given more channels to use than exist: "
        f"Given {len(use_channels)} channels to use but liquid handler "
        f"only has {self.backend.num_channels}."
      )

    if len(use_channels) != len(tip_spots):
      raise ValueError(
        f"Length mismatch: received {len(use_channels)} channels for "
        f"{len(tip_spots)} tip spots. One channel must be assigned per tip spot."
      )

    presence_flags = [True] * len(tip_spots)
    z_height = tip_spots[0].get_location_wrt(self.deck, z="top").z + 5

    # Step 1: Cluster tip spots by x-coordinate
    clusters_by_x: Dict[float, List[Tuple[TipSpot, int, int]]] = {}
    for idx, tip_spot in enumerate(tip_spots):
      assert tip_spot.location is not None, "TipSpot location must be at a location"
      x = tip_spot.location.x
      clusters_by_x.setdefault(x, []).append((tip_spot, use_channels[idx], idx))

    sorted_clusters = [clusters_by_x[x] for x in sorted(clusters_by_x)]

    # Step 2: Probe each cluster
    for cluster in sorted_clusters:
      tip_subset, channel_subset, index_subset = zip(*cluster)

      try:
        await self.pick_up_tips(
          list(tip_subset),
          use_channels=list(channel_subset),
          minimum_traverse_height_at_beginning_of_a_command=z_height,
          z_position_at_end_of_a_command=z_height,
        )
      except ChannelizedError as e:
        for ch in e.errors:
          if ch in channel_subset:
            failed_local_idx = channel_subset.index(ch)
            presence_flags[index_subset[failed_local_idx]] = False
          else:
            raise

      # Step 3: Drop tips immediately after probing
      if any(presence_flags[index] for index in index_subset):
        spots = [ts for ts, _, i in cluster if presence_flags[i]]
        use_channels = [uc for _, uc, i in cluster if presence_flags[i]]
        try:
          await self.drop_tips(
            spots,
            use_channels=use_channels,
            # minimum_traverse_height_at_beginning_of_a_command=z_height,
            z_position_at_end_of_a_command=z_height,
          )
        except Exception as e:
          assert cluster[0][0].location is not None, "TipSpot location must be at a location"
          print(f"Warning: drop_tips failed for cluster at x={cluster[0][0].location.x}: {e}")

    return {ts.name: flag for ts, flag in zip(tip_spots, presence_flags)}

  async def probe_tip_inventory(
    self,
    tip_spots: List[TipSpot],
    probing_fn: Optional[TipPresenceProbingMethod] = None,
    use_channels: Optional[List[int]] = None,
  ) -> Dict[str, bool]:
    """Probe the presence of tips in multiple tip spots.

    The provided ``probing_fn`` is used for probing batches of tip spots. The
    default uses :meth:`probe_tip_presence_via_pickup`.

    Examples:
      Probe all tip spots in one or more tip racks.

      >>> import pylabrobot.resources.functional as F
      >>> spots = F.get_all_tip_spots([tip_rack_1, tip_rack_2])
      >>> presence = await lh.probe_tip_inventory(spots)

    Args:
      tip_spots:
        Tip spots to probe for presence of a tip.
      probing_fn:
        Function used to probe a batch of tip spots. Must accept ``tip_spots`` and
        ``use_channels`` and return a mapping of tip spot names to boolean flags.

    Returns:
      Mapping from tip spot names to whether a tip is present.
    """

    if probing_fn is None:
      probing_fn = self.probe_tip_presence_via_pickup

    results: Dict[str, bool] = {}

    if use_channels is None:
      use_channels = list(range(self.backend.num_channels))
    num_channels = len(use_channels)

    for i in range(0, len(tip_spots), num_channels):
      subset = tip_spots[i : i + num_channels]
      use_channels = use_channels[: len(subset)]
      batch_result = await probing_fn(subset, use_channels)
      results.update(batch_result)

    return results

  async def consolidate_tip_inventory(
    self, tip_racks: List[TipRack], use_channels: Optional[List[int]] = None
  ):
    """
    Consolidate partial tip racks on the deck by redistributing tips.

    This function identifies partially-filled tip racks (excluding any in
    `ignore_tiprack_list`) in the 'tip_inventory`, the subset of the deck tree
    that is of type TipRack, and consolidates their tips into as few tip racks
    as possible, grouped by tip model.
    Tips are moved efficiently to minimize pipetting steps, avoiding redundant
    visits to the same drop columns.

    Args:
      tip_racks: List of TipRack objects to consolidate.
      use_channels: Optional list of channels to use for consolidation. If not
        provided, the first 8 available channels will be used.
    """

    def merge_sublists(lists: List[List[TipSpot]], max_len: int) -> List[List[TipSpot]]:
      """Merge adjacent sublists if combined length <= max_len, without splitting sublists."""
      merged: List[List[TipSpot]] = []
      buffer: List[TipSpot] = []

      for sublist in lists:
        if len(sublist) == 0:
          continue  # skip empty sublists

        if len(buffer) + len(sublist) <= max_len:
          buffer.extend(sublist)
        else:
          if buffer:
            merged.append(buffer)
          buffer = sublist  # start new buffer

      if len(buffer) > 0:
        merged.append(buffer)

      return merged

    def divide_list_into_chunks(
      list_l: List[TipSpot], chunk_size: int
    ) -> Generator[List[TipSpot], None, None]:
      """Divides a list into smaller chunks of a specified size.

      Parameters:
        - list_l: The list to be divided into chunks.
        - chunk_size: The size of each chunk.

      Returns:
        A generator that yields chunks of the list.
      """
      for i in range(0, len(list_l), chunk_size):
        yield list_l[i : i + chunk_size]

    clusters_by_model: Dict[int, List[Tuple[TipRack, int]]] = {}

    for idx, tip_rack in enumerate(tip_racks):
      # Only consider partially-filled tip_racks
      tip_status = [tip_spot.tracker.has_tip for tip_spot in tip_rack.get_all_items()]

      if not (any(tip_status) and not all(tip_status)):
        continue  # ignore non-partially-filled tip_racks

      tipspots_w_tips = [
        tip_spot for has_tip, tip_spot in zip(tip_status, tip_rack.get_all_items()) if has_tip
      ]

      # Identify model by hashed unique physical characteristics
      current_model = hash(tipspots_w_tips[0].tracker.get_tip())
      if not all(
        hash(tip_spot.tracker.get_tip()) == current_model for tip_spot in tipspots_w_tips[1:]
      ):
        raise ValueError(
          f"Tip rack {tip_rack.name} has mixed tip models, cannot consolidate: "
          f"{[tip_spot.tracker.get_tip() for tip_spot in tipspots_w_tips]}"
        )

      num_empty_tipspots = len(tip_status) - len(tipspots_w_tips)
      clusters_by_model.setdefault(current_model, []).append((tip_rack, num_empty_tipspots))

    # Sort partially-filled tipracks from most to least empty
    for model, rack_list in clusters_by_model.items():
      rack_list.sort(key=lambda x: x[1])

    # Consolidate one tip model at a time across all tip_racks of that model
    for model, rack_list in clusters_by_model.items():
      print(f"Consolidating: - {', '.join([rack.name for rack, _ in rack_list])}")

      all_tip_spots_list = [
        tip_spot for tip_rack, _ in rack_list for tip_spot in tip_rack.get_all_items()
      ]

      # 1: Record current tip state
      current_tip_presence_list = [tip_spot.has_tip() for tip_spot in all_tip_spots_list]

      # 2: Generate target/consolidated tip state
      total_length = len(all_tip_spots_list)
      num_tips_per_model = sum(current_tip_presence_list)

      target_tip_presence_list = [i < num_tips_per_model for i in range(total_length)]

      # 3: Calculate tip_spots involved in tip movement
      tip_movement_list = [
        c - t for c, t in zip(current_tip_presence_list, target_tip_presence_list)
      ]

      tip_origin_indices = [i for i, v in enumerate(tip_movement_list) if v == 1]
      all_origin_tip_spots = [all_tip_spots_list[idx] for idx in tip_origin_indices]

      tip_target_indices = [i for i, v in enumerate(tip_movement_list) if v == -1]
      all_target_tip_spots = [all_tip_spots_list[idx] for idx in tip_target_indices]

      # Only continue if tip_racks are not already consolidated
      if len(all_target_tip_spots) == 0:
        print("Tips already optimally consolidated!")
        continue

      # 4: Cluster target tip_spots by BOTH parent tip_rack & x-coordinate
      def key_for_tip_spot(tip_spot: TipSpot) -> Tuple[str, float]:
        """Key function to sort tip spots by parent name and x-coordinate."""
        assert tip_spot.parent is not None and tip_spot.location is not None
        return (tip_spot.parent.name, round(tip_spot.location.x, 3))

      sorted_tip_spots = sorted(all_target_tip_spots, key=key_for_tip_spot)

      target_tip_clusters_by_parent_x: Dict[Tuple[str, float], List[TipSpot]] = {}

      for tip_spot in sorted_tip_spots:
        key = key_for_tip_spot(tip_spot)
        if key not in target_tip_clusters_by_parent_x:
          target_tip_clusters_by_parent_x[key] = []
        target_tip_clusters_by_parent_x[key].append(tip_spot)

      current_tip_model = all_origin_tip_spots[0].tracker.get_tip()

      # Ensure there are channels that can pick up the tip model
      if use_channels is None:
        num_channels_available = len(
          [
            c
            for c in range(self.backend.num_channels)
            if self.backend.can_pick_up_tip(c, current_tip_model)
          ]
        )
        use_channels = list(range(num_channels_available))
      num_channels_available = len(use_channels)

      # 5: Optimize speed
      if num_channels_available == 0:
        raise ValueError(f"No channel capable of handling tips on deck: {current_tip_model}")

      # by aggregating drop columns i.e. same drop column should not be visited twice!
      if num_channels_available >= 8:  # physical constraint of tip_rack's having 8 rows
        merged_target_tip_clusters = merge_sublists(
          list(target_tip_clusters_by_parent_x.values()), max_len=8
        )
      else:  # by chunking drop tip_spots list into size of available channels
        merged_target_tip_clusters = list(
          divide_list_into_chunks(all_target_tip_spots, chunk_size=num_channels_available)
        )

      len_transfers = len(merged_target_tip_clusters)

      # 6: Execute tip movement/consolidation
      for idx, target_tip_spots in enumerate(merged_target_tip_clusters):
        print(f"   - tip transfer cycle: {idx+1} / {len_transfers}")

        origin_tip_spots = [all_origin_tip_spots.pop(0) for _ in range(len(target_tip_spots))]

        these_channels = use_channels[: len(target_tip_spots)]
        await self.pick_up_tips(origin_tip_spots, use_channels=these_channels)
        await self.drop_tips(target_tip_spots, use_channels=these_channels)
