""" Defines LiquidHandler class, the coordinator for liquid handling operations. """

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import numbers
import threading
import time
from typing import Any, Callable, Dict, Union, Optional, List, Sequence, Set
import warnings

from pylabrobot.default import Defaultable, Default
from pylabrobot.liquid_handling.channel_tip_tracker import ChannelHasNoTipError
from pylabrobot.liquid_handling.strictness import Strictness, get_strictness
from pylabrobot.liquid_handling.channel_tip_tracker import ChannelTipTracker
from pylabrobot.plate_reading import PlateReader
from pylabrobot.resources import (
  Container,
  Deck,
  Resource,
  ResourceStack,
  Coordinate,
  CarrierSite,
  Lid,
  Plate,
  Tip,
  TipRack,
  TipSpot,
  Well,
  does_tip_tracking,
  does_volume_tracking
)
from pylabrobot.utils.list import expand

from .backends import LiquidHandlerBackend
from .standard import (
  Pickup,
  PickupTipRack,
  Drop,
  DropTipRack,
  Aspiration,
  AspirationPlate,
  Dispense,
  DispensePlate,
  Move,
  GripDirection
)

logger = logging.getLogger("pylabrobot")


def need_setup_finished(func: Callable): # pylint: disable=no-self-argument
  """ Decorator for methods that require the liquid handler to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the liquid handler is not set up.
  """

  @functools.wraps(func)
  async def wrapper(self, *args, **kwargs):
    if not self.setup_finished:
      raise RuntimeError("The setup has not finished. See `LiquidHandler.setup`.")
    await func(self, *args, **kwargs) # pylint: disable=not-callable
  return wrapper


class LiquidHandler:
  """
  Front end for liquid handlers.

  This class is the front end for liquid handlers; it provides a high-level interface for
  interacting with liquid handlers. In the background, this class uses the low-level backend (
  defined in `pyhamilton.liquid_handling.backends`) to communicate with the liquid handler.

  Attributes:
    setup_finished: Whether the liquid handler has been setup.
  """

  def __init__(self, backend: LiquidHandlerBackend, deck: Deck):
    """ Initialize a LiquidHandler.

    Args:
      backend: Backend to use.
      deck: Deck to use.
    """

    self.backend = backend
    self.setup_finished = False
    self._picked_up_tips96: Optional[TipRack] = None # TODO: replace with tracker.

    self.deck = deck
    self.deck.resource_assigned_callback_callback = self.resource_assigned_callback
    self.deck.resource_unassigned_callback_callback = self.resource_unassigned_callback

    self.head: Dict[int, ChannelTipTracker] = {}

  async def setup(self):
    """ Prepare the robot for use. """

    if self.setup_finished:
      raise RuntimeError("The setup has already finished. See `LiquidHandler.stop`.")

    await self.backend.setup()
    self.setup_finished = True

    self.head = {c: ChannelTipTracker() for c in range(self.backend.num_channels)}

    self.resource_assigned_callback(self.deck)
    for resource in self.deck.children:
      self.resource_assigned_callback(resource)

  def update_head_state(self, state: Dict[int, Optional[Tip]]):
    """ Update the state of the liquid handler head.

    All keys in `state` must be valid channels. Channels for which no key is specified will keep
    their current state.

    Args:
      state: A dictionary mapping channels to tips. If a channel is mapped to None, that channel
        will have no tip.
    """

    assert set(state.keys()).issubset(set(self.head.keys())), "Invalid channel."

    for channel, tip in state.items():
      self.head[channel].set_tip(tip)

  def clear_head_state(self):
    """ Clear the state of the liquid handler head. """

    self.update_head_state({c: None for c in self.head.keys()})

  async def stop(self):
    await self.backend.stop()
    self.setup_finished = False

  def _run_async_in_thread(self, func, *args, **kwargs):
    def callback(*args, **kwargs):
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      loop.run_until_complete(func(*args, **kwargs))

    t = threading.Thread(target=callback, args=args, kwargs=kwargs)
    t.start()
    t.join()

  def resource_assigned_callback(self, resource: Resource):
    self._run_async_in_thread(self.backend.assigned_resource_callback, resource)

  def resource_unassigned_callback(self, resource: Resource):
    self._run_async_in_thread(self.backend.unassigned_resource_callback, resource.name)

  def unassign_resource(self, resource: Union[str, Resource]): # TODO: remove this.
    """ Unassign an assigned resource.

    Args:
      resource: The resource to unassign.

    Raises:
      KeyError: If the resource is not currently assigned to this liquid handler.
    """

    if isinstance(resource, Resource):
      resource = resource.name

    r = self.deck.get_resource(resource)
    if r is None:
      raise KeyError(f"Resource '{resource}' is not assigned to this liquid handler.")
    r.unassign()

  def get_resource(self, name: str) -> Resource:
    """ Find a resource on the deck of this liquid handler. Also see :meth:`~Deck.get_resource`.

    Args:
      name: name of the resource.

    Returns:
      The resource with the given name, or None if not found.
    """

    return self.deck.get_resource(name)

  def summary(self):
    """ Prints a string summary of the deck layout.  """

    print(self.deck.summary())

  def _assert_positions_unique(self, positions: List[str]):
    """ Returns whether all items in `positions` are unique where they are not `None`.

    Args:
      positions: List of positions.
    """

    not_none = [p for p in positions if p is not None]
    if len(not_none) != len(set(not_none)):
      raise ValueError("Positions must be unique.")

  def _assert_resources_exist(self, resources: Sequence[Resource]):
    """ Checks that each resource in `resources` is assigned to the deck.

    Args:
      resources: List of resources.

    Raises:
      ValueError: If a resource is not assigned to the deck.
    """

    for resource in resources:
      # see of top parent of resource is the deck (i.e. resource is assigned to deck)
      while resource.parent is not None:
        resource = resource.parent
      if resource != self.deck:
        raise ValueError(f"Resource named '{resource.name}' not found on deck.")

  def _check_args(
    self,
    method: Callable,
    backend_kwargs: Dict[str, Any],
    default: Set[str]
  ) -> Set[str]:
    """ Checks that the arguments to `method` are valid.

    Args:
      method: Method to check.
      backend_kwargs: Keyword arguments to `method`.

    Raises:
      TypeError: If the arguments are invalid.

    Returns:
      The set of arguments that need to be removed from `backend_kwargs` before passing to `method`.
    """

    default_args = default.union({"self"})

    sig = inspect.signature(method)
    args = {arg: param for arg, param in sig.parameters.items() if arg not in default_args}
    vars_keyword = {arg for arg, param in sig.parameters.items() # **kwargs
                    if param.kind == inspect.Parameter.VAR_KEYWORD}
    args = {arg: param for arg, param in args.items() # filter *args and **kwargs
            if param.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}}
    non_default = {arg for arg, param in args.items() if param.default == inspect.Parameter.empty}

    strictness = get_strictness()

    backend_kws = set(backend_kwargs.keys())

    missing = non_default - backend_kws
    if len(missing) > 0:
      raise TypeError(f"Missing arguments to backend.{method.__name__}: {missing}")

    extra = backend_kws - non_default
    if len(extra) > 0 and len(vars_keyword) == 0:
      if strictness == Strictness.STRICT:
        raise TypeError(f"Extra arguments to backend.{method.__name__}: {extra}")
      elif strictness == Strictness.WARN:
        warnings.warn(f"Extra arguments to backend.{method.__name__}: {extra}")
      else:
        logger.debug("Extra arguments to backend.%s: %s", method.__name__, extra)

    return extra

  @need_setup_finished
  async def pick_up_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Defaultable[Union[Coordinate, List[Defaultable[Coordinate]]]] = Default,
    **backend_kwargs
  ):
    """ Pick up tips from a resource.

    Examples:
      Pick up all tips in the first column.

      >>> lh.pick_up_tips(tips_resource["A1":"H1"])

      Pick up tips on odd numbered rows, skipping the other channels.

      >>> lh.pick_up_tips(tips_resource["A1", "C1", "E1", "G1"],use_channels=[0, 2, 4, 6])

      Pick up tips from different tip resources:

      >>> lh.pick_up_tips(tips_resource1["A1"] + tips_resource2["B2"] + tips_resource3["C3"])

      Picking up tips with different offsets:

      >>> lh.pick_up_tips(
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
      offsets: List of offsets for each channel, a translation that will be applied to the tip
        drop location. If `None`, no offset will be applied.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the positions are not unique.

      ChannelHasTipError: If a channel already has a tip.

      TipSpotHasNoTipError: If a spot does not have a tip.
    """

    self._assert_resources_exist(tip_spots)

    offsets = expand(offsets, len(tip_spots))

    if use_channels is None:
      use_channels = list(range(len(tip_spots)))

    assert len(tip_spots) == len(offsets) == len(use_channels), \
      "Number of tips and offsets and use_channels must be equal."

    tips: List[Tip] = []
    for tip_spot in tip_spots:
      tips.append(tip_spot.get_tip())

    pickups = [Pickup(resource=tip_spot, offset=offset, tip=tip)
               for tip_spot, offset, tip in zip(tip_spots, offsets, tips)]

    for channel, op in zip(use_channels, pickups):
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        op.resource.tracker.queue_pickup(op)
      self.head[channel].queue_pickup(op)

    extras = self._check_args(self.backend.pick_up_tips, backend_kwargs,
      default={"ops", "use_channels"})
    for extra in extras:
      del backend_kwargs[extra]

    try:
      await self.backend.pick_up_tips(ops=pickups, use_channels=use_channels, **backend_kwargs)
    except:
      for channel, op in zip(use_channels, pickups):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.rollback()
        self.head[channel].rollback()
      raise
    else:
      for channel, op in zip(use_channels, pickups):
        if does_tip_tracking() and not op.resource.tracker.is_disabled:
          op.resource.tracker.commit()
        self.head[channel].commit()

  @need_setup_finished
  async def drop_tips(
    self,
    tip_spots: List[Union[TipSpot, Resource]],
    use_channels: Optional[List[int]] = None,
    offsets: Defaultable[Union[Coordinate, List[Defaultable[Coordinate]]]] = Default,
    allow_nonzero_volume: bool = False,
    **backend_kwargs
  ):
    """ Drop tips to a resource.

    Examples:
      Dropping tips to the first column.

      >>> lh.pick_up_tips(tip_rack["A1:H1"])

      Dropping tips with different offsets:

      >>> lh.drop_tips(
      ...   channels=tips_resource["A1":"C1"],
      ...   offsets=[
      ...     Coordinate(0, 0, 0), # A1
      ...     Coordinate(1, 1, 1), # B1
      ...     Coordinate.zero() # C1
      ...   ]
      ... )

    Args:
      tips: Tip resource locations to drop to.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      offsets: List of offsets for each channel, a translation that will be applied to the tip
        pickup location. If `None`, no offset will be applied.
      allow_nonzero_volume: If `True`, the tip will be dropped even if its volume is not zero (there
        is liquid in the tip). If `False`, a RuntimeError will be raised if the tip has nonzero
        volume.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If no channel will pick up a tip, in other words, if all channels are `None` or
        if the list of channels is empty.

      ValueError: If the positions are not unique.

      ChannelHasNoTipError: If a channel does not have a tip.

      TipSpotHasTipError: If a spot already has a tip.
    """

    self._assert_resources_exist(tip_spots)

    offsets = expand(offsets, len(tip_spots))

    if use_channels is None:
      use_channels = list(range(len(tip_spots)))

    tips = []
    for channel in use_channels:
      tip = self.head[channel].get_tip()
      if tip is None:
        raise ChannelHasNoTipError(channel)
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
        raise RuntimeError(f"Cannot drop tip with volume {tip.tracker.get_used_volume()}")
      tips.append(tip)

    assert len(tip_spots) == len(offsets) == len(use_channels) == len(tips), \
      "Number of channels and offsets and use_channels and tips must be equal."

    drops = [Drop(resource=tip_spot, offset=offset, tip=tip)
             for tip_spot, tip, offset in zip(tip_spots, tips, offsets)]

    for channel, op in zip(use_channels, drops):
      if does_tip_tracking() and isinstance(op.resource, TipSpot) and \
          not op.resource.tracker.is_disabled:
        op.resource.tracker.queue_drop(op)
      self.head[channel].queue_drop(op)

    extras = self._check_args(self.backend.drop_tips, backend_kwargs,
      default={"ops", "use_channels"})
    for extra in extras:
      del backend_kwargs[extra]

    try:
      await self.backend.drop_tips(ops=drops, use_channels=use_channels, **backend_kwargs)
    except:
      for channel, op in zip(use_channels, drops):
        if does_tip_tracking() and \
          (isinstance(op.resource, TipSpot) and not op.resource.tracker.is_disabled):
          op.resource.tracker.rollback()
        self.head[channel].rollback()
      raise
    else:
      for channel, op in zip(use_channels, drops):
        if does_tip_tracking() and \
          (isinstance(op.resource, TipSpot) and not op.resource.tracker.is_disabled):
          op.resource.tracker.commit()
        self.head[channel].commit()

  async def return_tips(self):
    """ Return all tips that are currently picked up to their original place.

    Examples:
      Return the tips on the head to the tip rack where they were picked up:

      >>> lh.pick_up_tips(tip_rack["A1"])
      >>> lh.return_tips()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    tip_spots: List[TipSpot] = []
    channels: List[int] = []

    for channel, tracker in self.head.items():
      if tracker.has_tip:
        tip_spots.append(tracker.get_last_pickup_location())
        channels.append(channel)

    if len(tip_spots) == 0:
      raise RuntimeError("No tips have been picked up.")

    return await self.drop_tips(tip_spots=tip_spots, use_channels=channels)

  async def discard_tips(
    self,
    use_channels: Optional[List[int]] = None,
    **backend_kwargs
  ):
    """ Permanently discard tips.

    Examples:
      Discarding the tips on channels 1 and 2:

      >>> lh.discard_tips(use_channels=[0, 1])

      Discarding all tips currently picked up:

      >>> lh.discard_tips()

    Args:
      use_channels: List of channels to use. Index from front to back. If `None`, all that have
        tips will be used.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    # Different default value from drop_tips: here we factor in the tip tracking.
    if use_channels is None:
      use_channels = [c for c, t in self.head.items() if t.has_tip]

    n = len(use_channels)

    if n == 0:
      raise RuntimeError("No tips have been picked up and no channels were specified.")

    trash = self.deck.get_trash_area()
    offsets = trash.get_2d_center_offsets(n=n)
    offsets = list(reversed(offsets))

    return await self.drop_tips(
        tip_spots=[trash]*n,
        use_channels=use_channels,
        offsets=offsets,
        **backend_kwargs)

  @need_setup_finished
  async def aspirate(
    self,
    resources: Union[Container, Sequence[Container]],
    vols: Union[List[float], float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Defaultable[Union[float, List[Defaultable[float]]]] = Default,
    end_delay: float = 0,
    offsets: Union[Defaultable[Coordinate], Sequence[Defaultable[Coordinate]]] = Default,
    liquid_height: Union[Defaultable[float], List[Defaultable[float]]] = Default,
    **backend_kwargs
  ):
    """ Aspirate liquid from the specified wells.

    Examples:
      Aspirate a constant amount of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], 50)

      Aspirate an linearly increasing amount of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], range(0, 500, 50))

      Aspirate arbitrary amounts of liquid from the first column:

      >>> lh.aspirate(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Aspirate liquid from wells in different plates:

      >>> lh.aspirate(plate["A1"] + plate2["A1"] + plate3["A1"], 50)

      Aspirating with a 10mm z-offset:

      >>> lh.aspirate(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Aspirate from a blue bucket (big container), with the first 4 channels (which will be
      spaced equally apart):

      >>> lh.aspirate(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      resources: A list of wells to aspirate liquid from. Can be a single resource, or a list of
        resources. If a single resource is specified, all channels will aspirate from the same
        resource.
      vols: A list of volumes to aspirate, one for each channel. If `vols` is a single number, then
        all channels will aspirate that volume.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(wells)` channels will be used.
      flow_rates: the aspiration speed. In ul/s. If `Default`, the backend default will be used.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      offsets: List of offsets for each channel, a translation that will be applied to the
        aspiration location. If `None`, no offset will be applied.
      liquid_height: The height of the liquid in the well, in mm.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If all channels are `None`.
    """

    # Start with computing the locations of the aspirations. Can either be a single resource, in
    # which case all channels will aspirate from there, or a list of resources.
    if isinstance(resources, Resource): # if single resource, space channels evenly
      if use_channels is None:
        use_channels = [0]

      n = len(use_channels)

      # If offsets is supplied, make sure it is a list of the correct length. If it is not in this
      # format, raise an error. If it is not supplied, make it a list of the correct length by
      # spreading channels across the resource evenly.
      if offsets is not Default:
        if not isinstance(offsets, list) or len(offsets) != n:
          raise ValueError("Number of offsets must match number of channels used when aspirating "
                           "from a resource.")
      else:
        offsets = resources.get_2d_center_offsets(n=n)
        offsets = list(reversed(offsets))

      resources = [resources] * n
    else:
      if len(resources) == 0:
        raise ValueError("No channels specified")
      self._assert_resources_exist(resources)
      n = len(resources)

      for resource in resources:
        if isinstance(resource.parent, Plate) and resource.parent.has_lid():
          raise ValueError("Aspirating from plate with lid")

      if use_channels is None:
        use_channels = list(range(len(resources)))

      offsets = expand(offsets, n)

    vols = expand(vols, n)
    flow_rates = expand(flow_rates, n)
    liquid_height = expand(liquid_height, n)
    tips = [self.head[channel].get_tip() for channel in use_channels]

    assert len(vols) == len(offsets) == len(flow_rates) == len(liquid_height)

    aspirations = [Aspiration(r, v, offset=o, flow_rate=fr, liquid_height=lh, tip=t)
                   for r, v, o, fr, lh, t in
                    zip(resources, vols, offsets, flow_rates, liquid_height, tips)]

    for op in aspirations:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.queue_aspiration(op)
        op.tip.tracker.queue_aspiration(op)

    extras = self._check_args(self.backend.aspirate, backend_kwargs,
      default={"ops", "use_channels"})
    for extra in extras:
      del backend_kwargs[extra]

    try:
      await self.backend.aspirate(ops=aspirations, use_channels=use_channels, **backend_kwargs)
    except:
      for op in aspirations:
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.rollback()
          op.tip.tracker.rollback()
      raise
    else:
      for op in aspirations:
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.commit()
          op.tip.tracker.commit()

    if end_delay > 0:
      time.sleep(end_delay)

  @need_setup_finished
  async def dispense(
    self,
    resources: Union[Container, Sequence[Container]],
    vols: Union[List[float], float],
    use_channels: Optional[List[int]] = None,
    flow_rates: Defaultable[Union[float, List[Defaultable[float]]]] = Default,
    end_delay: float = 0,
    offsets: Union[Defaultable[Coordinate], Sequence[Defaultable[Coordinate]]] = Default,
    liquid_height: Union[Defaultable[float], List[Defaultable[float]]] = Default,
    **backend_kwargs
  ):
    """ Dispense liquid to the specified channels.

    Examples:
      Dispense a constant amount of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], 50)

      Dispense an linearly increasing amount of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], range(0, 500, 50))

      Dispense arbitrary amounts of liquid to the first column:

      >>> lh.dispense(plate["A1:H1"], [0, 40, 10, 50, 100, 200, 300, 400])

      Dispense liquid to wells in different plates:

      >>> lh.dispense((plate["A1"], 50), (plate2["A1"], 50), (plate3["A1"], 50))

      Dispensing with a 10mm z-offset:

      >>> lh.dispense(plate["A1"], vols=50, offsets=[Coordinate(0, 0, 10)])

      Dispense a blue bucket (big container), with the first 4 channels (which will be spaced
      equally apart):

      >>> lh.dispense(blue_bucket, vols=50, use_channels=[0, 1, 2, 3])

    Args:
      wells: A list of resources to dispense liquid to. Can be a list of resources, or a single
        resource, in which case all channels will dispense to that resource.
      vols: A list of volumes to dispense, one for each channel, or a single volume to dispense to
        all channels. If `vols` is a single number, then all channels will dispense that volume. In
        units of ul.
      use_channels: List of channels to use. Index from front to back. If `None`, the first
        `len(channels)` channels will be used.
      flow_rates: the flow rates, in ul/s. If `Default`, the backend default will be used.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      offsets: List of offsets for each channel, a translation that will be applied to the
        dispense location. If `None`, no offset will be applied.
      liquid_height: The height of the liquid in the well, in mm.
      backend_kwargs: Additional keyword arguments for the backend, optional.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.

      ValueError: If the dispense info is invalid, in other words, when all channels are `None`.

      ValueError: If all channels are `None`.
    """

    # Start with computing the locations of the dispenses. Can either be a single resource, in
    # which case all channels will dispense to there, or a list of resources.
    if isinstance(resources, Resource): # if single resource, space channels evenly
      if use_channels is None:
        use_channels = [0]

      n = len(use_channels)

      # If offsets is supplied, make sure it is a list of the correct length. If it is not in this
      # format, raise an error. If it is not supplied, make it a list of the correct length by
      # spreading channels across the resource evenly.
      if offsets is not Default:
        if not isinstance(offsets, list) or len(offsets) != n:
          raise ValueError("Number of offsets must match number of channels used when dispensing "
                          "to a resource.")
      else:
        offsets = resources.get_2d_center_offsets(n=n)
        offsets = list(reversed(offsets))

      resources = [resources] * n
    else:
      if len(resources) == 0:
        raise ValueError("No channels specified")
      self._assert_resources_exist(resources)
      n = len(resources)

      for resource in resources:
        if isinstance(resource.parent, Plate) and resource.parent.has_lid():
          raise ValueError("Dispensing to plate with lid")

      if use_channels is None:
        use_channels = list(range(len(resources)))

      offsets = expand(offsets, n)

    vols = expand(vols, n)
    flow_rates = expand(flow_rates, n)
    liquid_height = expand(liquid_height, n)
    tips = [self.head[channel].get_tip() for channel in use_channels]

    assert len(vols) == len(offsets) == len(flow_rates) == len(liquid_height)

    dispenses = [Dispense(r, v, offset=o, flow_rate=fr, liquid_height=lh, tip=t)
                 for r, v, o, fr, lh, t in
                  zip(resources, vols, offsets, flow_rates, liquid_height, tips)]

    for op in dispenses:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.queue_dispense(op)
        op.tip.tracker.queue_dispense(op)

    extras = self._check_args(self.backend.dispense, backend_kwargs,
      default={"ops", "use_channels"})
    for extra in extras:
      del backend_kwargs[extra]

    try:
      await self.backend.dispense(ops=dispenses, use_channels=use_channels, **backend_kwargs)
    except:
      for op in dispenses:
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.rollback()
          op.tip.tracker.rollback()
      raise
    else:
      for op in dispenses:
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.commit()
          op.tip.tracker.commit()

    if end_delay > 0:
      time.sleep(end_delay)

  async def transfer(
    self,
    source: Well,
    targets: Union[Well, List[Well]],
    source_vol: Optional[float] = None,
    ratios: Optional[List[float]] = None,
    target_vols: Optional[List[float]] = None,
    aspiration_flow_rate: Defaultable[float] = Default,
    dispense_flow_rates: Defaultable[Union[float, List[Defaultable[float]]]] = Default,
    **backend_kwargs
  ):
    """Transfer liquid from one well to another.

    Examples:

      Transfer 50 uL of liquid from the first well to the second well:

      >>> lh.transfer(plate["A1"], plate["B1"], source_vol=50)

      Transfer 80 uL of liquid from the first well equally to the first column:

      >>> lh.transfer(plate["A1"], plate["A1:H1"], source_vol=80)

      Transfer 60 uL of liquid from the first well in a 1:2 ratio to 2 other wells:

      >>> lh.transfer(plate["A1"], plate["B1:C1"], source_vol=60, ratios=[2, 1])

      Transfer arbitrary volumes to the first column:

      >>> lh.transfer(plate["A1"], plate["A1:H1"], target_vols=[3, 1, 4, 1, 5, 9, 6, 2])

    Args:
      source: The source well.
      targets: The target wells.
      source_vol: The volume to transfer from the source well.
      ratios: The ratios to use when transferring liquid to the target wells. If not specified, then
        the volumes will be distributed equally.
      target_vols: The volumes to transfer to the target wells. If specified, `source_vols` and
        `ratios` must be `None`.
      aspiration_flow_rate: The flow rate to use when aspirating, in ul/s. If `Default`, the backend
        default will be used.
      dispense_flow_rates: The flow rates to use when dispensing, in ul/s. If `Default`, the backend
        default will be used. Either a single flow rate for all channels, or a list of flow rates,
        one for each target well.

    Raises:
      RuntimeError: If the setup has not been run. See :meth:`~LiquidHandler.setup`.
    """

    if isinstance(targets, Well):
      targets = [targets]

    if isinstance(dispense_flow_rates, numbers.Rational):
      dispense_flow_rates = [dispense_flow_rates] * len(targets)

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
      flow_rates=aspiration_flow_rate,
      **backend_kwargs)
    for target, vol in zip(targets, target_vols):
      await self.dispense(
        resources=[target],
        vols=vol,
        flow_rates=dispense_flow_rates,
        use_channels=[0],
        **backend_kwargs)

  async def pick_up_tips96(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    **backend_kwargs):
    """ Pick up tips using the 96 head. This will pick up 96 tips.

    Examples:
      Pick up tips from a 96-tip tiprack:

      >>> lh.pick_up_tips96(my_tiprack)

    Args:
      tip_rack: The tip rack to pick up tips from.
      offset: The offset to use when picking up tips, optional.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    extras = self._check_args(self.backend.pick_up_tips96, backend_kwargs, default={"pickup"})
    for extra in extras:
      del backend_kwargs[extra]

    await self.backend.pick_up_tips96(
      pickup=PickupTipRack(resource=tip_rack, offset=offset),
      **backend_kwargs
    )

    # Save the tips as picked up.
    self._picked_up_tips96 = tip_rack

  async def drop_tips96(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    **backend_kwargs
  ):
    """ Drop tips using the 96 head. This will drop 96 tips.

    Examples:
      Drop tips to a 96-tip tiprack:

      >>> lh.drop_tips96(my_tiprack)

    Args:
      tip_rack: The tip rack to drop tips to.
      offset: The offset to use when dropping tips.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    extras = self._check_args(self.backend.drop_tips96, backend_kwargs, default={"drop"})
    for extra in extras:
      del backend_kwargs[extra]

    await self.backend.drop_tips96(
      drop=DropTipRack(resource=tip_rack, offset=offset),
      **backend_kwargs
    )

    self._picked_up_tips96 = None

  async def return_tips96(self):
    """ Return the tips on the 96 head to the tip rack where they were picked up.

    Examples:
      Return the tips on the 96 head to the tip rack where they were picked up:

      >>> lh.pick_up_tips96(my_tiprack)
      >>> lh.return_tips96()

    Raises:
      RuntimeError: If no tips have been picked up.
    """

    if self._picked_up_tips96 is None:
      raise RuntimeError("No tips have been picked up with the 96 head")

    await self.drop_tips96(self._picked_up_tips96)

  async def aspirate_plate(
    self,
    plate: Plate,
    volume: float,
    flow_rate: Defaultable[float] = Default,
    end_delay: float = 0,
    **backend_kwargs
  ):
    """ Aspirate from all wells in a plate.

    Examples:
      Aspirate an entire 96 well plate:

      >>> lh.aspirate_plate(plate, volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to aspirate from each well.
      flow_rate: The flow rate to use when aspirating, in ul/s. If `Default`, the backend default
        will be used.
      end_delay: The delay after the last aspiration in seconds, optional. This is useful for when
        the tips used in the aspiration are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    extras = self._check_args(self.backend.aspirate96, backend_kwargs, default={"aspiration"})
    for extra in extras:
      del backend_kwargs[extra]

    if self._picked_up_tips96 is None:
      raise ChannelHasNoTipError("No tips have been picked up with the 96 head")
    tips = self._picked_up_tips96.get_all_tips()

    if plate.has_lid():
      raise ValueError("Aspirating from plate with lid")

    if plate.num_items_x == 12 and plate.num_items_y == 8:
      await self.backend.aspirate96(aspiration=AspirationPlate(
        resource=plate,
        volume=volume,
        flow_rate=flow_rate,
        tips=tips),
        **backend_kwargs)
    else:
      raise NotImplementedError(f"It is not possible to plate aspirate from an {plate.num_items_x} "
                               f"by {plate.num_items_y} plate")

    if end_delay > 0:
      time.sleep(end_delay)

  async def dispense_plate(
    self,
    plate: Plate,
    volume: float,
    flow_rate: Defaultable[float] = Default,
    end_delay: float = 0,
    **backend_kwargs
  ):
    """ Dispense to all wells in a plate.

    Examples:
      Dispense an entire 96 well plate:

      >>> dispense96(plate, volume=50)

    Args:
      resource: Resource name or resource object.
      pattern: Either a list of lists of booleans where inner lists represent rows and outer lists
        represent columns, or a string representing a range of positions. Default all.
      volume: The volume to dispense to each well.
      flow_rate: The flow rate to use when aspirating, in ul/s. If `Default`, the backend default
        will be used.
      end_delay: The delay after the last dispense in seconds, optional. This is useful for when
        the tips used in the dispense are dripping.
      backend_kwargs: Additional keyword arguments for the backend, optional.
    """

    extras = self._check_args(self.backend.dispense96, backend_kwargs, default={"dispense"})
    for extra in extras:
      del backend_kwargs[extra]

    if self._picked_up_tips96 is None:
      raise ChannelHasNoTipError("No tips have been picked up with the 96 head")
    tips = self._picked_up_tips96.get_all_tips()

    if plate.has_lid():
      raise ValueError("Dispensing to plate with lid")

    if plate.num_items_x == 12 and plate.num_items_y == 8:
      await self.backend.dispense96(dispense=DispensePlate(
        resource=plate,
        volume=volume,
        flow_rate=flow_rate,
        tips=tips),
        **backend_kwargs)
    else:
      raise NotImplementedError(f"It is not possible to plate dispense to an {plate.num_items_x} "
                               f"by {plate.num_items_y} plate")

    if end_delay > 0:
      time.sleep(end_delay)

  async def stamp(
    self,
    source: Plate,
    target: Plate,
    volume: float,
    aspiration_flow_rate: Defaultable[float] = Default,
    dispense_flow_rate: Defaultable[float] = Default,
  ):
    """ Stamp (aspiration and dispense) one plate onto another.

    Args:
      source: the source plate
      target: the target plate
      volume: the volume to be transported
      aspiration_flow_rate: the flow rate for the aspiration, in ul/s. If `Default`, the backend
        default will be used.
      dispense_flow_rate: the flow rate for the dispense, in ul/s. If `Default`, the backend default
        will be used.
    """

    assert (source.num_items_x, source.num_items_y) == (target.num_items_x, target.num_items_y), \
      "Source and target plates must be the same shape"

    await self.aspirate_plate(
      plate=source,
      volume=volume,
      flow_rate=aspiration_flow_rate)
    await self.dispense_plate(
      plate=source,
      volume=volume,
      flow_rate=dispense_flow_rate)

  async def move_resource(
    self,
    resource: Resource,
    to: Coordinate,
    intermediate_locations: Optional[List[Coordinate]] = None,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: float = 0,
    get_direction: GripDirection = GripDirection.FRONT,
    put_direction: GripDirection = GripDirection.FRONT,
    **backend_kwargs
  ):
    """ Move a resource to a new location.

    Examples:
      Move a plate to a new location:

      >>> lh.move_resource(plate, to=Coordinate(100, 100, 100))

    Args:
      resource: The Resource object.
      to: The absolute coordinate (meaning relative to deck) to move the resource to.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).
      pickup_distance_from_top: The distance from the top of the resource to pick up from.
      get_direction: The direction from which to pick up the resource.
      put_direction: The direction from which to put down the resource.
    """

    extras = self._check_args(self.backend.move_resource, backend_kwargs, default={"move"})
    for extra in extras:
      del backend_kwargs[extra]

    return await self.backend.move_resource(move=Move(
      resource=resource,
      to=to,
      intermediate_locations=intermediate_locations,
      resource_offset=resource_offset,
      to_offset=to_offset,
      pickup_distance_from_top=pickup_distance_from_top,
      get_direction=get_direction,
      put_direction=put_direction),
      **backend_kwargs)

  async def move_lid(
    self,
    lid: Lid,
    to: Union[Plate, ResourceStack, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    get_direction: GripDirection = GripDirection.FRONT,
    put_direction: GripDirection = GripDirection.FRONT,
    pickup_distance_from_top: float = 5.7,
    **backend_kwargs
  ):
    """ Move a lid to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a lid to the :class:`~resources.ResourceStack`:

      >>> lh.move_lid(plate.lid, stacking_area)

      Move a lid to the stacking area and back, grabbing it from the left side:

      >>> lh.move_lid(plate.lid, stacking_area, get_direction=GripDirection.LEFT)
      >>> lh.move_lid(stacking_area.get_top_item(), plate, put_direction=GripDirection.LEFT)

    Args:
      lid: The lid to move. Can be either a Plate object or a Lid object.
      to: The location to move the lid to, either a plate, ResourceStack or a Coordinate.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).

    Raises:
      ValueError: If the lid is not assigned to a resource.
    """

    if isinstance(to, Plate):
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z() - lid.get_size_z())
    elif isinstance(to, ResourceStack):
      assert to.direction == "z", "Only ResourceStacks with direction 'z' are currently supported"
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z())
    elif isinstance(to, Coordinate):
      to_location = to
    else:
      raise ValueError(f"Cannot move lid to {to}")

    await self.move_resource(
      lid,
      to=to_location,
      intermediate_locations=intermediate_locations,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_offset=resource_offset,
      to_offset=to_offset,
      get_direction=get_direction,
      put_direction=put_direction,
      **backend_kwargs)

    lid.unassign()
    if isinstance(to, Coordinate):
      self.deck.assign_child_resource(lid, location=to_location)
    elif isinstance(to, ResourceStack): # manage its own resources
      to.assign_child_resource(lid)
    else:
      to.assign_child_resource(lid, location=to_location)

  async def move_plate(
    self,
    plate: Plate,
    to: Union[ResourceStack, CarrierSite, Resource, Coordinate],
    intermediate_locations: Optional[List[Coordinate]] = None,
    resource_offset: Coordinate = Coordinate.zero(),
    to_offset: Coordinate = Coordinate.zero(),
    put_direction: GripDirection = GripDirection.FRONT,
    get_direction: GripDirection = GripDirection.FRONT,
    pickup_distance_from_top: float = 13.2,
    **backend_kwargs
  ):
    """ Move a plate to a new location.

    A convenience method for :meth:`move_resource`.

    Examples:
      Move a plate to into a carrier spot:

      >>> lh.move_plate(plate, plt_car[1])

      Move a plate to an absolute location:

      >>> lh.move_plate(plate_01, Coordinate(100, 100, 100))

      Move a lid to another carrier spot, grabbing it from the left side:

      >>> lh.move_plate(plate, plt_car[1], get_direction=GripDirection.LEFT)
      >>> lh.move_plate(plate, plt_car[0], put_direction=GripDirection.LEFT)

      Move a resource while visiting a few intermediate locations along the way:

      >>> lh.move_plate(plate, plt_car[1], intermediate_locations=[
      ...   Coordinate(100, 100, 100),
      ...   Coordinate(200, 200, 200),
      ... ])

    Args:
      plate: The plate to move. Can be either a Plate object or a CarrierSite object.
      to: The location to move the plate to, either a plate, CarrierSite or a Coordinate.
      resource_offset: The offset from the resource's origin, optional (rarely necessary).
      to_offset: The offset from the location's origin, optional (rarely necessary).
    """

    if isinstance(to, ResourceStack):
      assert to.direction == "z", "Only ResourceStacks with direction 'z' are currently supported"
      to_location = to.get_absolute_location()
      to_location = Coordinate(
        x=to_location.x,
        y=to_location.y,
        z=to_location.z  + to.get_size_z())
    elif isinstance(to, Coordinate):
      to_location = to
    else:
      to_location = to.get_absolute_location()

    await self.move_resource(
      plate,
      to=to_location,
      intermediate_locations=intermediate_locations,
      pickup_distance_from_top=pickup_distance_from_top,
      resource_offset=resource_offset,
      to_offset=to_offset,
      get_direction=get_direction,
      put_direction=put_direction,
      **backend_kwargs)

    plate.unassign()
    if isinstance(to, Coordinate):
      to_location -= self.deck.location # passed as an absolute location, but stored as relative
      self.deck.assign_child_resource(plate, location=to_location)
    elif isinstance(to, CarrierSite): # .zero() resources
      to.assign_child_resource(plate, location=Coordinate.zero())
    elif isinstance(to, (ResourceStack, PlateReader)): # manage its own resources
      to.assign_child_resource(plate)
    else:
      to.assign_child_resource(plate, location=to_location)

  def serialize(self) -> dict:
    """ Serialize the liquid handler to a dictionary.

    Returns:
      A dictionary representation of the liquid handler.
    """

    return {
      "deck": self.deck.serialize(),
      "backend": self.backend.serialize()
    }

  def save(self, path: str):
    """ Save the liquid handler to a file.

    Args:
      path: The path to the file to save to.
    """

    with open(path, "w", encoding="utf-8") as f:
      json.dump(self.serialize(), f, indent=2)

  @classmethod
  def deserialize(cls, data: dict) -> LiquidHandler:
    """ Deserialize a liquid handler from a dictionary.

    Args:
      data: A dictionary representation of the liquid handler.
    """

    deck = Deck.deserialize(data["deck"])
    backend = LiquidHandlerBackend.deserialize(data["backend"])
    return LiquidHandler(deck=deck, backend=backend)

  @classmethod
  def load(cls, path: str) -> LiquidHandler:
    """ Load a liquid handler from a file.

    Args:
      path: The path to the file to load from.
    """

    with open(path, "r", encoding="utf-8") as f:
      return cls.deserialize(json.load(f))
