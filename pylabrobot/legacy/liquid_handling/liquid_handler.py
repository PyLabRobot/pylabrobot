"""Defines LiquidHandler class, the coordinator for liquid handling operations."""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import unittest.mock
import warnings
from dataclasses import dataclass, field
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
)

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.head96 import Head96Capability
from pylabrobot.capabilities.liquid_handling.head96_backend import (
  Head96Backend as _NewHead96Backend,
)
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.capabilities.liquid_handling.pip_backend import (
  PIPBackend as _NewLHBackend,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Aspiration as _NewAspiration,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Dispense as _NewDispense,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  DropTipRack as _NewDropTipRack,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Mix as _NewMix,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadAspirationContainer as _NewMHAC,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadAspirationPlate as _NewMHAP,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadDispenseContainer as _NewMHDC,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  MultiHeadDispensePlate as _NewMHDP,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  Pickup as _NewPickup,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  PickupTipRack as _NewPickupTipRack,
)
from pylabrobot.capabilities.liquid_handling.standard import (
  TipDrop as _NewTipDrop,
)
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.legacy.liquid_handling.strictness import (
  Strictness,
  get_strictness,
)
from pylabrobot.legacy.liquid_handling.utils import (
  get_tight_single_resource_liquid_op_offsets,
)
from pylabrobot.legacy.machines.machine import Machine, need_setup_finished
from pylabrobot.legacy.plate_reading import PlateReader
from pylabrobot.legacy.tilting.tilter import Tilter
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
)
from pylabrobot.resources.rotation import Rotation
from pylabrobot.serializer import deserialize, serialize

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


def _convert_mix(new_mix) -> Optional[Mix]:
  """Convert a new-style Mix to a legacy Mix."""
  if new_mix is None:
    return None
  return Mix(volume=new_mix.volume, repetitions=new_mix.repetitions, flow_rate=new_mix.flow_rate)


class BlowOutVolumeError(Exception):
  pass


# ---------------------------------------------------------------------------
# Legacy → new adapters
# ---------------------------------------------------------------------------


@dataclass
class _DictBackendParams(BackendParams):
  """Wraps legacy **backend_kwargs into a BackendParams for the new capability interface."""

  kwargs: Dict[str, Any] = field(default_factory=dict)


class _LHAdapter(_NewLHBackend):
  """Adapts legacy LiquidHandlerBackend to new LiquidHandlerBackend."""

  def __init__(self, legacy: LiquidHandlerBackend):
    self._legacy = legacy

  @property
  def num_channels(self) -> int:
    return self._legacy.num_channels

  async def pick_up_tips(
    self,
    ops: List[_NewPickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    legacy_ops = [Pickup(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.pick_up_tips(ops=legacy_ops, use_channels=use_channels, **kw)

  async def drop_tips(
    self,
    ops: List[_NewTipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    legacy_ops = [Drop(resource=op.resource, offset=op.offset, tip=op.tip) for op in ops]
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.drop_tips(ops=legacy_ops, use_channels=use_channels, **kw)

  async def aspirate(
    self,
    ops: List[_NewAspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    legacy_ops = [
      SingleChannelAspiration(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=_convert_mix(op.mix),
      )
      for op in ops
    ]
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.aspirate(ops=legacy_ops, use_channels=use_channels, **kw)

  async def dispense(
    self,
    ops: List[_NewDispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    legacy_ops = [
      SingleChannelDispense(
        resource=op.resource,
        offset=op.offset,
        tip=op.tip,
        volume=op.volume,
        flow_rate=op.flow_rate,
        liquid_height=op.liquid_height,
        blow_out_air_volume=op.blow_out_air_volume,
        mix=_convert_mix(op.mix),
      )
      for op in ops
    ]
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.dispense(ops=legacy_ops, use_channels=use_channels, **kw)

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return self._legacy.can_pick_up_tip(channel_idx, tip)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    return await self._legacy.request_tip_presence()


class _Head96Adapter(_NewHead96Backend):
  """Adapts legacy LiquidHandlerBackend to new Head96Backend."""

  def __init__(self, legacy: LiquidHandlerBackend):
    self._legacy = legacy

  async def pick_up_tips96(
    self, pickup: _NewPickupTipRack, backend_params: Optional[BackendParams] = None
  ):
    legacy_pickup = PickupTipRack(resource=pickup.resource, offset=pickup.offset, tips=pickup.tips)
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.pick_up_tips96(pickup=legacy_pickup, **kw)

  async def drop_tips96(
    self, drop: _NewDropTipRack, backend_params: Optional[BackendParams] = None
  ):
    legacy_drop = DropTipRack(resource=drop.resource, offset=drop.offset)
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.drop_tips96(drop=legacy_drop, **kw)

  async def aspirate96(
    self,
    aspiration: Union[_NewMHAP, _NewMHAC],
    backend_params: Optional[BackendParams] = None,
  ):
    if isinstance(aspiration, _NewMHAP):
      legacy_asp: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer] = (
        MultiHeadAspirationPlate(
          wells=aspiration.wells,
          offset=aspiration.offset,
          tips=aspiration.tips,
          volume=aspiration.volume,
          flow_rate=aspiration.flow_rate,
          liquid_height=aspiration.liquid_height,
          blow_out_air_volume=aspiration.blow_out_air_volume,
          mix=_convert_mix(aspiration.mix),
        )
      )
    else:
      legacy_asp = MultiHeadAspirationContainer(
        container=aspiration.container,
        offset=aspiration.offset,
        tips=aspiration.tips,
        volume=aspiration.volume,
        flow_rate=aspiration.flow_rate,
        liquid_height=aspiration.liquid_height,
        blow_out_air_volume=aspiration.blow_out_air_volume,
        mix=_convert_mix(aspiration.mix),
      )
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.aspirate96(aspiration=legacy_asp, **kw)

  async def dispense96(
    self,
    dispense: Union[_NewMHDP, _NewMHDC],
    backend_params: Optional[BackendParams] = None,
  ):
    if isinstance(dispense, _NewMHDP):
      legacy_disp: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer] = (
        MultiHeadDispensePlate(
          wells=dispense.wells,
          offset=dispense.offset,
          tips=dispense.tips,
          volume=dispense.volume,
          flow_rate=dispense.flow_rate,
          liquid_height=dispense.liquid_height,
          blow_out_air_volume=dispense.blow_out_air_volume,
          mix=_convert_mix(dispense.mix),
        )
      )
    else:
      legacy_disp = MultiHeadDispenseContainer(
        container=dispense.container,
        offset=dispense.offset,
        tips=dispense.tips,
        volume=dispense.volume,
        flow_rate=dispense.flow_rate,
        liquid_height=dispense.liquid_height,
        blow_out_air_volume=dispense.blow_out_air_volume,
        mix=_convert_mix(dispense.mix),
      )
    kw = backend_params.kwargs if isinstance(backend_params, _DictBackendParams) else {}
    await self._legacy.dispense96(dispense=legacy_disp, **kw)


# ---------------------------------------------------------------------------
# LiquidHandler
# ---------------------------------------------------------------------------


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

    # New capability instances — created during setup()
    self._lh_cap: Optional[PIP] = None
    self._head96_cap: Optional[Head96Capability] = None

    # Default offset applied to all 96-head operations. Any offset passed to a 96-head method is
    # added to this value.
    self.default_offset_head96: Coordinate = default_offset_head96 or Coordinate.zero()

    # assign deck as only child resource, and set location of self to origin.
    self.location = Coordinate.zero()
    super().assign_child_resource(deck, location=deck.location or Coordinate.zero())

    self._resource_pickups: Dict[int, Optional[ResourcePickup]] = {}

  @property
  def _resource_pickup(self) -> Optional[ResourcePickup]:
    """Backward-compatible access to the first arm's pickup state."""
    return self._resource_pickups.get(0)

  @_resource_pickup.setter
  def _resource_pickup(self, value: Optional[ResourcePickup]) -> None:
    self._resource_pickups[0] = value

  async def setup(self, **backend_kwargs):
    """Prepare the robot for use."""

    if self.setup_finished:
      raise RuntimeError("The setup has already finished. See `LiquidHandler.stop`.")

    self.backend.set_deck(self.deck)
    self.backend.set_heads(head=self.head, head96=self.head96)
    await super().setup(**backend_kwargs)

    # Create capabilities with adapter backends
    self._lh_cap = PIP(backend=_LHAdapter(self.backend))
    await self._lh_cap._on_setup()

    if self.backend.head96_installed:
      self._head96_cap = Head96Capability(
        backend=_Head96Adapter(self.backend),
      )
      await self._head96_cap._on_setup()

    # Alias head trackers from capabilities for backward compat
    self.head = self._lh_cap.head
    self.head96 = self._head96_cap.head if self._head96_cap is not None else {}

    self.backend.set_heads(head=self.head, head96=self.head96 or None)

    for tracker in self.head.values():
      tracker.register_callback(self._state_updated)
    for tracker in self.head96.values():
      tracker.register_callback(self._state_updated)

    self._resource_pickups = {a: None for a in range(self.backend.num_arms)}

  def serialize_state(self) -> Dict[str, Any]:
    """Serialize the state of this liquid handler. Use :meth:`~Resource.serialize_all_states` to
    serialize the state of the liquid handler and all children (the deck)."""

    head_state = {channel: tracker.serialize() for channel, tracker in self.head.items()}
    head96_state = (
      {channel: tracker.serialize() for channel, tracker in self.head96.items()}
      if self.head96
      else None
    )
    arm_state: Optional[Dict[int, Any]]
    if self._resource_pickups:
      arm_state = {
        arm_id: serialize(pickup) if pickup is not None else None
        for arm_id, pickup in self._resource_pickups.items()
      }
    else:
      arm_state = None
    return {"head_state": head_state, "head96_state": head96_state, "arm_state": arm_state}

  def load_state(self, state: Dict[str, Any]):
    """Load the liquid handler state from a file. Use :meth:`~Resource.load_all_state` to load the
    state of the liquid handler and all children (the deck)."""

    head_state = state["head_state"]
    for channel, tracker_state in head_state.items():
      self.head[channel].load_state(tracker_state)

    head96_state = state.get("head96_state", {})
    if head96_state and self.head96:
      for channel, tracker_state in head96_state.items():
        self.head96[channel].load_state(tracker_state)

    # arm_state is informational only (read via serialize_state); no load needed since
    # _resource_pickup is set/cleared by pick_up_resource/drop_resource at runtime.

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

    self._assert_resources_exist(tip_spots)

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.pick_up_tips,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._lh_cap is not None
    await self._lh_cap.pick_up_tips(
      tip_spots=tip_spots,
      use_channels=use_channels,
      offsets=offsets,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )

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

    self._assert_resources_exist(tip_spots)

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.drop_tips,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._lh_cap is not None
    await self._lh_cap.drop_tips(
      tip_spots=tip_spots,
      use_channels=use_channels,
      offsets=offsets,
      allow_nonzero_volume=allow_nonzero_volume,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )

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

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.aspirate,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._lh_cap is not None
    await self._lh_cap.aspirate(
      resources=resources,
      vols=vols,
      use_channels=use_channels,
      flow_rates=flow_rates,
      offsets=offsets,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      spread=spread,
      mix=[_NewMix(volume=m.volume, repetitions=m.repetitions, flow_rate=m.flow_rate) for m in mix]
      if mix is not None
      else None,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
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

    # fix the backend kwargs
    extras = self._check_args(
      self.backend.dispense,
      backend_kwargs,
      default={"ops", "use_channels"},
      strictness=get_strictness(),
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._lh_cap is not None
    await self._lh_cap.dispense(
      resources=resources,
      vols=vols,
      use_channels=use_channels,
      flow_rates=flow_rates,
      offsets=offsets,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      spread=spread,
      mix=[_NewMix(volume=m.volume, repetitions=m.repetitions, flow_rate=m.flow_rate) for m in mix]
      if mix is not None
      else None,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
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
    if self._lh_cap is not None:
      self._lh_cap._default_use_channels = channels

    try:
      yield
    finally:
      self._default_use_channels = None
      if self._lh_cap is not None:
        self._lh_cap._default_use_channels = None

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

    extras = self._check_args(
      self.backend.pick_up_tips96, backend_kwargs, default={"pickup"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._head96_cap is not None
    await self._head96_cap.pick_up_tips(
      tip_rack=tip_rack,
      offset=offset,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
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

    extras = self._check_args(
      self.backend.drop_tips96, backend_kwargs, default={"drop"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._head96_cap is not None
    await self._head96_cap.drop_tips(
      resource=resource,
      offset=offset,
      allow_nonzero_volume=allow_nonzero_volume,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
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

    extras = self._check_args(
      self.backend.aspirate96, backend_kwargs, default={"aspiration"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._head96_cap is not None
    await self._head96_cap.aspirate(
      resource=resource,
      volume=volume,
      offset=offset,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=_NewMix(volume=mix.volume, repetitions=mix.repetitions, flow_rate=mix.flow_rate)
      if mix is not None
      else None,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
    )

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

    extras = self._check_args(
      self.backend.dispense96, backend_kwargs, default={"dispense"}, strictness=get_strictness()
    )
    for extra in extras:
      del backend_kwargs[extra]

    assert self._head96_cap is not None
    await self._head96_cap.dispense(
      resource=resource,
      volume=volume,
      offset=offset,
      flow_rate=flow_rate,
      liquid_height=liquid_height,
      blow_out_air_volume=blow_out_air_volume,
      mix=_NewMix(volume=mix.volume, repetitions=mix.repetitions, flow_rate=mix.flow_rate)
      if mix is not None
      else None,
      backend_params=_DictBackendParams(kwargs=backend_kwargs) if backend_kwargs else None,
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
    await self.dispense96(resource=target, volume=volume, flow_rate=dispense_flow_rate)

  async def pick_up_resource(
    self,
    resource: Resource,
    offset: Coordinate = Coordinate.zero(),
    pickup_distance_from_top: Optional[float] = None,
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

    if self.setup_finished and not self._resource_pickups:
      raise RuntimeError("No robotic arm is installed on this liquid handler.")

    if pickup_distance_from_top is None:
      if resource.preferred_pickup_location is not None:
        logger.debug(
          f"Using preferred pickup location for resource {resource.name} as pickup_distance_from_top was not specified."
        )
        pickup_distance_from_top = resource.get_size_z() - resource.preferred_pickup_location.z
      else:
        logger.debug(
          f"No preferred pickup location for resource {resource.name}. Using default pickup distance of 5mm."
        )
        pickup_distance_from_top = 5.0

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

    self._state_updated()

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

    if isinstance(destination, Resource):
      destination.check_can_drop_resource_here(resource)

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
      assert destination.direction == "z", (
        "Only ResourceStacks with direction 'z' are currently supported"
      )

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
    self._state_updated()

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

  def serialize(self) -> dict:
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
        print(f"   - tip transfer cycle: {idx + 1} / {len_transfers}")

        origin_tip_spots = [all_origin_tip_spots.pop(0) for _ in range(len(target_tip_spots))]

        these_channels = use_channels[: len(target_tip_spots)]
        await self.pick_up_tips(origin_tip_spots, use_channels=these_channels)
        await self.drop_tips(target_tip_spots, use_channels=these_channels)
