"""Defines LiquidHandler class, the coordinator for liquid handling operations."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import threading
import warnings
from typing import (
  Any,
  Callable,
  Dict,
  List,
  Literal,
  Optional,
  Protocol,
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
  VolumeTracker,
  Well,
  does_cross_contamination_tracking,
  does_tip_tracking,
  does_volume_tracking,
)
from pylabrobot.resources.errors import CrossContaminationError, HasTipError
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.rotation import Rotation
from pylabrobot.tilting.tilter import Tilter

from .backends import LiquidHandlerBackend
from .standard import (
  Drop,
  DropTipRack,
  GripDirection,
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


def check_contaminated(liquid_history_tip, liquid_history_well):
  """Helper function used to check if adding a liquid to the container
  would result in cross contamination"""
  return not liquid_history_tip.issubset(liquid_history_well) and len(liquid_history_tip) > 0


def check_updatable(src_tracker: VolumeTracker, dest_tracker: VolumeTracker):
  """Helper function used to check if it is possible to update the
  liquid_history of src based on contents of dst"""
  return (
    not src_tracker.is_cross_contamination_tracking_disabled
    and not dest_tracker.is_cross_contamination_tracking_disabled
  )


class BlowOutVolumeError(Exception):
  pass


class LiquidHandler(Resource, Machine):
  """
  Front end for liquid handlers.

  This class is the front end for liquid handlers; it provides a high-level interface for
  interacting with liquid handlers. In the background, this class uses the low-level backend (
  defined in `pyhamilton.liquid_handling.backends`) to communicate with the liquid handler.
  """

  ALLOWED_CALLBACKS = {
    "aspirate",
    "aspirate96",
    "dispense",
    "dispense96",
    "drop_tips",
    "drop_tips96",
    "move_resource",
    "pick_up_tips",
    "pick_up_tips96",
  }

  def __init__(self, backend: LiquidHandlerBackend, deck: Deck):
    """Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
      deck: Deck to use.
    """

    Resource.__init__(
      self,
      name=f"lh_{deck.name}",
      size_x=deck._size_x,
      size_y=deck._size_y,
      size_z=deck._size_z,
      category="liquid_handler",
    )
    Machine.__init__(self, backend=backend)

    self.backend: LiquidHandlerBackend = backend  # fix type
    self._callbacks: Dict[str, OperationCallback] = {}

    self.deck = deck
    # register callbacks for sending resource assignment/unassignment to backend
    self.deck.register_did_assign_resource_callback(self._send_assigned_resource_to_backend)
    self.deck.register_did_unassign_resource_callback(self._send_unassigned_resource_to_backend)

    self.head: Dict[int, TipTracker] = {}
    self.head96: Dict[int, TipTracker] = {}
    self._default_use_channels: Optional[List[int]] = None

    self._blow_out_air_volume: Optional[List[Optional[float]]] = None

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

    self._send_assigned_resource_to_backend(self.deck)
    for resource in self.deck.children:
      self._send_assigned_resource_to_backend(resource)

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

  def _run_async_in_thread(self, func, *args, **kwargs):
    def callback(*args, **kwargs):
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      loop.run_until_complete(func(*args, **kwargs))
      loop.close()

    t = threading.Thread(target=callback, args=args, kwargs=kwargs)
    t.start()
    t.join()

  def _send_assigned_resource_to_backend(self, resource: Resource):
    """This method is called when a resource is assigned to the deck, and passes this information
    to the backend."""
    self._run_async_in_thread(self.backend.assigned_resource_callback, resource)

  def _send_unassigned_resource_to_backend(self, resource: Resource):
    """This method is called when a resource is unassigned from the deck, and passes this
    information to the backend."""
    self._run_async_in_thread(self.backend.unassigned_resource_callback, resource.name)

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

    not_tip_spots = [ts for ts in tip_spots if not isinstance(ts, TipSpot)]
    if len(not_tip_spots) > 0:
      raise TypeError(f"Resources must be `TipSpot`s, got {not_tip_spots}")

    # fix arguments
    if use_channels is None:
      if self._default_use_channels is None:
        use_channels = list(range(len(tip_spots)))
      else:
        use_channels = self._default_use_channels
    tips = [tip_spot.get_tip() for tip_spot in tip_spots]

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

    # trigger callback
    self._trigger_callback(
      "pick_up_tips",
      liquid_handler=self,
      operations=pickups,
      use_channels=use_channels,
      error=error,
      **backend_kwargs,
    )

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

    not_tip_spots = [ts for ts in tip_spots if not isinstance(ts, (TipSpot, Trash))]
    if len(not_tip_spots) > 0:
      raise TypeError(f"Resources must be `TipSpot`s or Trash, got {not_tip_spots}")

    # fix arguments
    if use_channels is None:
      if self._default_use_channels is None:
        use_channels = list(range(len(tip_spots)))
      else:
        use_channels = self._default_use_channels
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

    # trigger callback
    self._trigger_callback(
      "drop_tips",
      liquid_handler=self,
      operations=drops,
      use_channels=use_channels,
      error=error,
      **backend_kwargs,
    )

  async def return_tips(
    self,
    use_channels: Optional[list[int]] = None,
    allow_nonzero_volume: bool = False,
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
    spread: Literal["wide", "tight"] = "wide",
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
        apart as possible.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If all channels are `None`.
    """

    self._check_containers(resources)

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))

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
    assert len(resources) == len(vols) == len(offsets) == len(flow_rates) == len(liquid_height)

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
      else:  # wide
        center_offsets = get_wide_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )

      # add user defined offsets to the computed centers
      offsets = [c + o for c, o in zip(center_offsets, offsets)]

    # liquid(s) for each channel. If volume tracking is disabled, use None as the liquid.
    liquids: List[List[Tuple[Optional[Liquid], float]]] = []
    for r, vol in zip(resources, vols):
      if r.tracker.is_disabled or not does_volume_tracking():
        liquids.append([(None, vol)])
      else:
        liquids.append(r.tracker.get_liquids(top_volume=vol))

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
        liquids=lvs,
      )
      for r, v, o, fr, lh, t, bav, lvs in zip(
        resources,
        vols,
        offsets,
        flow_rates,
        liquid_height,
        tips,
        blow_out_air_volume,
        liquids,
      )
    ]

    # queue the operations on the resource (source) and mounted tips (destination) trackers
    for op in aspirations:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.remove_liquid(op.volume)

        # Cross contamination check
        if does_cross_contamination_tracking():
          if check_contaminated(
            op.tip.tracker.liquid_history,
            op.resource.tracker.liquid_history,
          ):
            raise CrossContaminationError(
              f"Attempting to aspirate {next(reversed(op.liquids))[0]} with a tip contaminated "
              f"with {op.tip.tracker.liquid_history}."
            )

        for liquid, volume in reversed(op.liquids):
          op.tip.tracker.add_liquid(liquid=liquid, volume=volume)

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
        (self.head[channel].get_tip().tracker.commit if success else self.head[channel].rollback)()

    # trigger callback
    self._trigger_callback(
      "aspirate",
      liquid_handler=self,
      operations=aspirations,
      use_channels=use_channels,
      error=error,
      **backend_kwargs,
    )

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
    spread: Literal["wide", "tight"] = "wide",
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

    # If the user specified a single resource, but multiple channels to use, we will assume they
    # want to space the channels evenly across the resource. Note that offsets are relative to the
    # center of the resource.

    self._check_containers(resources)

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))

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
      else:
        center_offsets = get_wide_single_resource_liquid_op_offsets(
          resource=resource, num_channels=len(use_channels)
        )

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

    assert len(vols) == len(offsets) == len(flow_rates) == len(liquid_height)

    # liquid(s) for each channel. If volume tracking is disabled, use None as the liquid.
    if does_volume_tracking():
      channels = [self.head[channel] for channel in use_channels]
      liquids = [c.get_tip().tracker.get_liquids(top_volume=vol) for c, vol in zip(channels, vols)]
    else:
      liquids = [[(None, vol)] for vol in vols]

    # create operations
    dispenses = [
      SingleChannelDispense(
        resource=r,
        volume=v,
        offset=o,
        flow_rate=fr,
        liquid_height=lh,
        tip=t,
        liquids=lvs,
        blow_out_air_volume=bav,
      )
      for r, v, o, fr, lh, t, bav, lvs in zip(
        resources,
        vols,
        offsets,
        flow_rates,
        liquid_height,
        tips,
        blow_out_air_volume,
        liquids,
      )
    ]

    # queue the operations on the resource (source) and mounted tips (destination) trackers
    for op in dispenses:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          # Update the liquid history of the tip to reflect new liquid
          if check_updatable(op.tip.tracker, op.resource.tracker):
            op.tip.tracker.liquid_history.update(op.resource.tracker.liquid_history)

          for liquid, volume in op.liquids:
            op.resource.tracker.add_liquid(liquid=liquid, volume=volume)
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
        (self.head[channel].get_tip().tracker.commit if success else self.head[channel].rollback)()

    if any(bav is not None for bav in blow_out_air_volume):
      self._blow_out_air_volume = None

    # trigger callback
    self._trigger_callback(
      "dispense",
      liquid_handler=self,
      operations=dispenses,
      use_channels=use_channels,
      error=error,
      **backend_kwargs,
    )

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
      offset: The offset to use when picking up tips, optional.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

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
    for i, tip_spot in enumerate(tip_rack.get_all_items()):
      if not does_tip_tracking() and self.head96[i].has_tip:
        self.head96[i].remove_tip()
      self.head96[i].add_tip(tip_spot.get_tip(), origin=tip_spot, commit=False)
      if does_tip_tracking() and not tip_spot.tracker.is_disabled:
        tip_spot.tracker.remove_tip()

    pickup_operation = PickupTipRack(resource=tip_rack, offset=offset)
    try:
      await self.backend.pick_up_tips96(pickup=pickup_operation, **backend_kwargs)
    except Exception as error:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.rollback()
        self.head96[i].rollback()
      self._trigger_callback(
        "pick_up_tips96",
        liquid_handler=self,
        pickup=pickup_operation,
        error=error,
        **backend_kwargs,
      )
    else:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.commit()
        self.head96[i].commit()
      self._trigger_callback(
        "pick_up_tips96",
        liquid_handler=self,
        pickup=pickup_operation,
        error=None,
        **backend_kwargs,
      )

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
      offset: The offset to use when dropping tips.
      allow_nonzero_volume: If `True`, the tip will be dropped even if its volume is not zero (there
        is liquid in the tip). If `False`, a RuntimeError will be raised if the tip has nonzero
        volume.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

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
      tip = self.head96[i].get_tip()
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
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
      self._trigger_callback(
        "drop_tips96",
        liquid_handler=self,
        drop=drop_operation,
        error=e,
        **backend_kwargs,
      )
    else:
      for i in range(96):
        if isinstance(resource, TipRack):
          tip_spot = resource.get_item(i)
          if does_tip_tracking() and not tip_spot.tracker.is_disabled:
            tip_spot.tracker.commit()
        self.head96[i].commit()
      self._trigger_callback(
        "drop_tips96",
        liquid_handler=self,
        drop=drop_operation,
        error=None,
        **backend_kwargs,
      )

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

  async def return_tips96(self, allow_nonzero_volume: bool = False, **backend_kwargs):
    """Return the tips on the 96 head to the tip rack where they were picked up.

    Examples:
      Return the tips on the 96 head to the tip rack where they were picked up:

      >>> await lh.pick_up_tips96(my_tiprack)
      >>> await lh.return_tips96()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    tip_rack = self._get_96_head_origin_tip_rack()
    if tip_rack is None:
      raise RuntimeError("No tips have been picked up with the 96 head")
    return await self.drop_tips96(
      tip_rack,
      allow_nonzero_volume=allow_nonzero_volume,
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

    return await self.drop_tips96(
      self.deck.get_trash_area96(),
      allow_nonzero_volume=allow_nonzero_volume,
      **backend_kwargs,
    )

  async def aspirate96(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    **backend_kwargs,
  ):
    """Aspirate from all wells in a plate or from a container of a sufficient size.

    Examples:
      Aspirate an entire 96 well plate or a container of sufficient size:

      >>> await lh.aspirate96(plate, volume=50)
      >>> await lh.aspirate96(container, volume=50)

    Args:
      resource (Union[Plate, Container, List[Well]]): Resource object or list of wells.
      volume (float): The volume to aspirate through each channel
      offset (Coordinate): Adjustment to where the 96 head should go to aspirate relative to where
        the plate or container is defined to be. Defaults to Coordinate.zero().
      flow_rate ([Optional[float]]): The flow rate to use when aspirating, in ul/s. If `None`, the
        backend default will be used.
      blow_out_air_volume ([Optional[float]]): The volume of air to aspirate after the liquid, in
        ul. If `None`, the backend default will be used.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

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

    tips = [channel.get_tip() for channel in self.head96.values()]
    all_liquids: List[List[Tuple[Optional[Liquid], float]]] = []
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]

    # Convert everything to floats to handle exotic number types
    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    containers: Sequence[Container]
    if isinstance(resource, Container):
      if (
        resource.get_absolute_size_x() < 108.0 or resource.get_absolute_size_y() < 70.0
      ):  # TODO: analyze as attr
        raise ValueError("Container too small to accommodate 96 head")

      for channel in self.head96.values():
        # superfluous to have append in two places but the type checker is very angry and does not
        # understand that Optional[Liquid] (remove_liquid) is the same as None from the first case
        liquids: List[Tuple[Optional[Liquid], float]]
        if resource.tracker.is_disabled or not does_volume_tracking():
          liquids = [(None, volume)]
          all_liquids.append(liquids)
        else:
          liquids = resource.tracker.remove_liquid(volume=volume)  # type: ignore
          all_liquids.append(liquids)

        for liquid, vol in reversed(liquids):
          channel.get_tip().tracker.add_liquid(liquid=liquid, volume=vol)

      aspiration = MultiHeadAspirationContainer(
        container=resource,
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=None,
        blow_out_air_volume=blow_out_air_volume,
        liquids=cast(List[List[Tuple[Optional[Liquid], float]]], all_liquids),  # stupid
      )

      containers = [resource]
    else:
      if isinstance(resource, Plate):
        if resource.has_lid():
          raise ValueError("Aspirating from plate with lid")
        containers = resource.get_all_items()
      else:
        containers = resource

        # ensure that wells are all in the same plate
        plate = containers[0].parent
        for well in containers:
          if well.parent != plate:
            raise ValueError("All wells must be in the same plate")

      if not len(containers) == 96:
        raise ValueError(f"aspirate96 expects 96 wells, got {len(containers)}")

      for well, channel in zip(containers, self.head96.values()):
        # superfluous to have append in two places but the type checker is very angry and does not
        # understand that Optional[Liquid] (remove_liquid) is the same as None from the first case
        if well.tracker.is_disabled or not does_volume_tracking():
          liquids = [(None, volume)]
          all_liquids.append(liquids)
        else:
          liquids = well.tracker.remove_liquid(volume=volume)  # type: ignore
          all_liquids.append(liquids)

        for liquid, vol in reversed(liquids):
          channel.get_tip().tracker.add_liquid(liquid=liquid, volume=vol)

      aspiration = MultiHeadAspirationPlate(
        wells=cast(List[Well], containers),
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=None,
        blow_out_air_volume=blow_out_air_volume,
        liquids=cast(List[List[Tuple[Optional[Liquid], float]]], all_liquids),  # stupid
      )

    try:
      await self.backend.aspirate96(aspiration=aspiration, **backend_kwargs)
    except Exception as error:
      for channel, container in zip(self.head96.values(), containers):
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.rollback()
        channel.get_tip().tracker.rollback()
      self._trigger_callback(
        "aspirate96",
        liquid_handler=self,
        aspiration=aspiration,
        error=error,
        **backend_kwargs,
      )
    else:
      for channel, container in zip(self.head96.values(), containers):
        if does_volume_tracking() and not container.tracker.is_disabled:
          container.tracker.commit()
      channel.get_tip().tracker.commit()

      self._trigger_callback(
        "aspirate96",
        liquid_handler=self,
        aspiration=aspiration,
        error=None,
        **backend_kwargs,
      )

  async def dispense96(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    **backend_kwargs,
  ):
    """Dispense to all wells in a plate.

    Examples:
      Dispense an entire 96 well plate:

      >>> await lh.dispense96(plate, volume=50)

    Args:
      resource (Union[Plate, Container, List[Well]]): Resource object or list of wells.
      volume (float): The volume to dispense through each channel
      offset (Coordinate): Adjustment to where the 96 head should go to aspirate relative to where
        the plate or container is defined to be. Defaults to Coordinate.zero().
      flow_rate ([Optional[float]]): The flow rate to use when dispensing, in ul/s. If `None`, the
        backend default will be used.
      blow_out_air_volume ([Optional[float]]): The volume of air to dispense after the liquid, in
        ul. If `None`, the backend default will be used.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

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

    tips = [channel.get_tip() for channel in self.head96.values()]
    all_liquids: List[List[Tuple[Optional[Liquid], float]]] = []
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]

    # Convert everything to floats to handle exotic number types
    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    containers: Sequence[Container]
    if isinstance(resource, Container):
      if (
        resource.get_absolute_size_x() < 108.0 or resource.get_absolute_size_y() < 70.0
      ):  # TODO: analyze as attr
        raise ValueError("Container too small to accommodate 96 head")

      for channel in self.head96.values():
        # superfluous to have append in two places but the type checker is very angry and does not
        # understand that Optional[Liquid] (remove_liquid) is the same as None from the first case
        reversed_liquids: List[Tuple[Optional[Liquid], float]]
        if resource.tracker.is_disabled or not does_volume_tracking():
          reversed_liquids = [(None, volume)]
          all_liquids.append(reversed_liquids)
        else:
          reversed_liquids = resource.tracker.remove_liquid(volume=volume)  # type: ignore
          all_liquids.append(reversed_liquids)

        for liquid, vol in reversed(reversed_liquids):
          channel.get_tip().tracker.add_liquid(liquid=liquid, volume=vol)

      dispense = MultiHeadDispenseContainer(
        container=resource,
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=None,
        blow_out_air_volume=blow_out_air_volume,
        liquids=cast(List[List[Tuple[Optional[Liquid], float]]], all_liquids),  # stupid
      )

      containers = [resource]
    else:
      if isinstance(resource, Plate):
        if resource.has_lid():
          raise ValueError("Aspirating from plate with lid")
        containers = resource.get_all_items()
      else:  # List[Well]
        containers = resource

        # ensure that wells are all in the same plate
        plate = containers[0].parent
        for well in containers:
          if well.parent != plate:
            raise ValueError("All wells must be in the same plate")

      if not len(containers) == 96:
        raise ValueError(f"dispense96 expects 96 wells, got {len(containers)}")

      for channel, well in zip(self.head96.values(), containers):
        # even if the volume tracker is disabled, a liquid (None, volume) is added to the list
        # during the aspiration command
        liquids = channel.get_tip().tracker.remove_liquid(volume=volume)
        reversed_liquids = list(reversed(liquids))
        all_liquids.append(reversed_liquids)

        for liquid, vol in reversed_liquids:
          well.tracker.add_liquid(liquid=liquid, volume=vol)

      dispense = MultiHeadDispensePlate(
        wells=cast(List[Well], containers),
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=None,
        blow_out_air_volume=blow_out_air_volume,
        liquids=all_liquids,
      )

    try:
      await self.backend.dispense96(dispense=dispense, **backend_kwargs)
    except Exception as error:
      for channel, container in zip(self.head96.values(), containers):
        if does_volume_tracking() and not well.tracker.is_disabled:
          container.tracker.rollback()
        channel.get_tip().tracker.rollback()

      self._trigger_callback(
        "dispense96",
        liquid_handler=self,
        dispense=dispense,
        error=error,
        **backend_kwargs,
      )
    else:
      for channel, container in zip(self.head96.values(), containers):
        if does_volume_tracking() and not well.tracker.is_disabled:
          container.tracker.commit()
        channel.get_tip().tracker.commit()

      self._trigger_callback(
        "dispense96",
        liquid_handler=self,
        dispense=dispense,
        error=None,
        **backend_kwargs,
      )

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
  ):
    if self._resource_pickup is None:
      raise RuntimeError("No resource picked up")
    await self.backend.move_picked_up_resource(
      ResourceMove(
        location=to,
        resource=self._resource_pickup.resource,
        gripped_direction=self._resource_pickup.direction,
      )
    )

  async def drop_resource(
    self,
    destination: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    offset: Coordinate = Coordinate.zero(),
    direction: GripDirection = GripDirection.FRONT,
    **backend_kwargs,
  ):
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
      to_location = destination.get_absolute_location(z="top")
    elif isinstance(destination, Coordinate):
      to_location = destination
    elif isinstance(destination, Tilter):
      to_location = destination.get_absolute_location() + destination.get_default_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      )
    elif isinstance(destination, PlateHolder):
      if destination.resource is not None and destination.resource is not resource:
        raise RuntimeError("Destination already has a plate")
      to_location = (destination.get_absolute_location()) + destination.get_default_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      )
    elif isinstance(destination, PlateAdapter):
      if not isinstance(resource, Plate):
        raise ValueError("Only plates can be moved to a PlateAdapter")
      # Calculate location adjustment of Plate based on PlateAdapter geometry
      adjusted_plate_anchor = destination.compute_plate_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      )
      to_location = destination.get_absolute_location() + adjusted_plate_anchor
    elif isinstance(destination, ResourceHolder):
      x = destination.get_default_child_location(
        resource.rotated(z=resource_rotation_wrt_destination_wrt_local)
      )
      to_location = destination.get_absolute_location() + x
    elif isinstance(destination, Plate) and isinstance(resource, Lid):
      lid = resource
      plate_location = destination.get_absolute_location()
      to_location = plate_location + destination.get_lid_location(
        lid.rotated(z=resource_rotation_wrt_destination_wrt_local)
      )
    else:
      to_location = destination.get_absolute_location()

    drop = ResourceDrop(
      resource=self._resource_pickup.resource,
      destination=to_location,
      destination_absolute_rotation=destination.get_absolute_rotation()
      if isinstance(destination, Resource)
      else Rotation(0, 0, 0),
      offset=offset,
      pickup_distance_from_top=self._resource_pickup.pickup_distance_from_top,
      pickup_direction=self._resource_pickup.direction,
      drop_direction=direction,
      rotation=rotation_applied_by_move,
    )
    result = await self.backend.drop_resource(drop=drop, **backend_kwargs)

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
    else:
      destination.assign_child_resource(resource, location=to_location)

    self._resource_pickup = None

    return result

  async def move_resource(
    self,
    resource: Resource,
    to: Union[ResourceStack, ResourceHolder, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    resource_offset: Optional[Coordinate] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    pickup_direction: GripDirection = GripDirection.FRONT,
    drop_direction: GripDirection = GripDirection.FRONT,
    get_direction: Optional[GripDirection] = None,
    put_direction: Optional[GripDirection] = None,
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

    # TODO: move conditional statements from move_plate into move_resource to enable
    # movement to other types besides Coordinate

    # https://github.com/PyLabRobot/pylabrobot/issues/329
    if resource_offset is not None:
      raise NotImplementedError("resource_offset is deprecated, use pickup_offset instead")
    if get_direction is not None:
      raise NotImplementedError("get_direction is deprecated, use pickup_direction instead")
    if put_direction is not None:
      raise NotImplementedError("put_direction is deprecated, use drop_direction instead")

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
    resource_offset: Optional[Coordinate] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    pickup_direction: GripDirection = GripDirection.FRONT,
    drop_direction: GripDirection = GripDirection.FRONT,
    get_direction: Optional[GripDirection] = None,
    put_direction: Optional[GripDirection] = None,
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

    # https://github.com/PyLabRobot/pylabrobot/issues/329
    if resource_offset is not None:
      raise NotImplementedError("resource_offset is deprecated, use pickup_offset instead")
    if get_direction is not None:
      raise NotImplementedError("get_direction is deprecated, use pickup_direction instead")
    if put_direction is not None:
      raise NotImplementedError("put_direction is deprecated, use drop_direction instead")

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
    resource_offset: Optional[Coordinate] = None,
    pickup_offset: Coordinate = Coordinate.zero(),
    destination_offset: Coordinate = Coordinate.zero(),
    drop_direction: GripDirection = GripDirection.FRONT,
    pickup_direction: GripDirection = GripDirection.FRONT,
    get_direction: Optional[GripDirection] = None,
    put_direction: Optional[GripDirection] = None,
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

    # https://github.com/PyLabRobot/pylabrobot/issues/329
    if resource_offset is not None:
      raise NotImplementedError("resource_offset is deprecated, use pickup_offset instead")
    if get_direction is not None:
      raise NotImplementedError("get_direction is deprecated, use pickup_direction instead")
    if put_direction is not None:
      raise NotImplementedError("put_direction is deprecated, use drop_direction instead")

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

  def register_callback(self, method_name: str, callback: OperationCallback):
    """Registers a callback for a specific method."""
    if method_name in self._callbacks:
      error_message = f"Callback already registered for: {method_name}"
      raise RuntimeError(error_message)
    if method_name not in self.ALLOWED_CALLBACKS:
      error_message = f"Callback not allowed: {method_name}"
      raise RuntimeError(error_message)
    self._callbacks[method_name] = callback

  def _trigger_callback(
    self,
    method_name: str,
    *args,
    error: Optional[Exception] = None,
    **kwargs,
  ):
    """Triggers the callback associated with a method, if any.

    NB: If an error exists it will be passed to the callback instead of being raised.
    """
    if callback := self._callbacks.get(method_name):
      callback(self, *args, error=error, **kwargs)
    elif error is not None:
      raise error

  @property
  def callbacks(self):
    return self._callbacks

  def serialize(self):
    return {**Resource.serialize(self), **Machine.serialize(self)}

  @classmethod
  def deserialize(cls, data: dict, allow_marshal: bool = False) -> LiquidHandler:
    """Deserialize a liquid handler from a dictionary.

    Args:
      data: A dictionary representation of the liquid handler.
    """

    deck_data = data["children"][0]
    deck = Deck.deserialize(data=deck_data, allow_marshal=allow_marshal)
    backend = LiquidHandlerBackend.deserialize(data=data["backend"])
    return cls(deck=deck, backend=backend)

  @classmethod
  def load(cls, path: str) -> LiquidHandler:
    """Load a liquid handler from a file.

    Args:
      path: The path to the file to load from.
    """

    with open(path, "r", encoding="utf-8") as f:
      return cls.deserialize(json.load(f))

  async def prepare_for_manual_channel_operation(self, channel: int):
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.prepare_for_manual_channel_operation(channel=channel)

  async def move_channel_x(self, channel: int, x: float):
    """Move channel to absolute x position"""
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.move_channel_x(channel=channel, x=x)

  async def move_channel_y(self, channel: int, y: float):
    """Move channel to absolute y position"""
    assert 0 <= channel < self.backend.num_channels, f"Invalid channel: {channel}"
    await self.backend.move_channel_y(channel=channel, y=y)

  async def move_channel_z(self, channel: int, z: float):
    """Move channel to absolute z position"""
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
      "Cannot assign child resource to liquid handler. Use "
      "lh.deck.assign_child_resource() instead."
    )


class OperationCallback(Protocol):
  def __call__(self, handler: "LiquidHandler", *args: Any, **kwargs: Any) -> None:
    ...  # pragma: no cover
