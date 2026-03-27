"""Capability for independent-channel liquid handling."""

import contextlib
import logging
from typing import Dict, Generator, List, Literal, Optional, Sequence, Union

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import (
  Container,
  Coordinate,
  Plate,
  Tip,
  TipSpot,
  TipTracker,
  Trash,
  Well,
  does_tip_tracking,
  does_volume_tracking,
)
from pylabrobot.resources.errors import HasTipError

from .errors import BlowOutVolumeError, ChannelizedError
from .pip_backend import PIPBackend
from .standard import Aspiration, Dispense, Mix, Pickup, TipDrop
from .utils import (
  get_tight_single_resource_liquid_op_offsets,
  get_wide_single_resource_liquid_op_offsets,
)

logger = logging.getLogger("pylabrobot")


class PIP(Capability):
  """Independent-channel liquid handling: pick up tips, aspirate, dispense, drop tips.

  Faithfully ports the tip tracking, volume tracking, validation, spread modes, and
  error handling from the legacy LiquidHandler frontend.
  """

  def __init__(self, backend: PIPBackend):
    super().__init__(backend=backend)
    self.backend: PIPBackend = backend
    self.head: Dict[int, TipTracker] = {}
    self._default_use_channels: Optional[List[int]] = None
    self._blow_out_air_volume: Optional[List[Optional[float]]] = None

  async def _on_setup(self):
    await super()._on_setup()
    self.head = {c: TipTracker(thing=f"Channel {c}") for c in range(self.backend.num_channels)}

  @property
  def num_channels(self) -> int:
    return self.backend.num_channels

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Get the tips currently mounted on the head.

    Returns:
      A list of tips, or None for channels without a tip.
    """
    return [tracker.get_tip() if tracker.has_tip else None for tracker in self.head.values()]

  def update_head_state(self, state: Dict[int, Optional[Tip]]):
    """Update the state of the head.

    All keys in `state` must be valid channels. Channels not in `state` keep their current state.

    Args:
      state: A dictionary mapping channels to tips. None means no tip.
    """
    if not set(state.keys()).issubset(set(self.head.keys())):
      raise ValueError("Invalid channel.")
    for channel, tip in state.items():
      if tip is None:
        if self.head[channel].has_tip:
          self.head[channel].remove_tip()
      else:
        if self.head[channel].has_tip:
          self.head[channel].remove_tip()
        self.head[channel].add_tip(tip)

  def clear_head_state(self):
    """Clear all tips from the head."""
    self.update_head_state({c: None for c in self.head.keys()})

  def serialize_state(self) -> Dict:
    """Serialize the head state for saving/restoring."""
    return {channel: tracker.serialize() for channel, tracker in self.head.items()}

  def load_state(self, state: Dict):
    """Load head state from a serialized dict."""
    for channel, tracker_state in state.items():
      self.head[channel].load_state(tracker_state)

  def _make_sure_channels_exist(self, channels: List[int]):
    invalid = [c for c in channels if c not in self.head]
    if invalid:
      raise ValueError(f"Invalid channels: {invalid}")

  @need_capability_ready
  async def pick_up_tips(
    self,
    tip_spots: List[TipSpot],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips from tip spots.

    Examples:
      Pick up all tips in the first column:

      >>> await lh.pick_up_tips(tips_resource["A1":"H1"])

      Pick up tips on odd rows, skipping the other channels:

      >>> await lh.pick_up_tips(tips_resource["A1", "C1", "E1", "G1"], use_channels=[0, 2, 4, 6])

    Args:
      tip_spots: List of tip spots to pick up tips from.
      use_channels: List of channels to use. If None, the first len(tip_spots) channels are used.
      offsets: List of offsets for each pickup. Defaults to zero.
      backend_params: Vendor-specific parameters.

    Raises:
      HasTipError: If a channel already has a tip.
      NoTipError: If a spot does not have a tip.
    """

    not_tip_spots = [ts for ts in tip_spots if not isinstance(ts, TipSpot)]
    if not_tip_spots:
      raise TypeError(f"Resources must be TipSpots, got {not_tip_spots}")

    use_channels = use_channels or self._default_use_channels or list(range(len(tip_spots)))
    if len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels must be unique.")

    tips = [tip_spot.get_tip() for tip_spot in tip_spots]
    offsets = offsets or [Coordinate.zero()] * len(tip_spots)

    # check tip compatibility
    if not all(
      self.backend.can_pick_up_tip(channel, tip) for channel, tip in zip(use_channels, tips)
    ):
      cannot = [
        ch for ch, tip in zip(use_channels, tips) if not self.backend.can_pick_up_tip(ch, tip)
      ]
      raise RuntimeError(f"Cannot pick up tips on channels {cannot}.")

    self._make_sure_channels_exist(use_channels)
    if not (len(tip_spots) == len(offsets) == len(use_channels)):
      raise ValueError("Number of tips, offsets, and use_channels must be equal.")

    pickups = [Pickup(resource=ts, offset=o, tip=t) for ts, o, t in zip(tip_spots, offsets, tips)]

    # queue operations on trackers
    for channel, op in zip(use_channels, pickups):
      if self.head[channel].has_tip:
        raise HasTipError("Channel has tip")
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        op.resource.tracker.remove_tip()
      self.head[channel].add_tip(op.tip, origin=op.resource, commit=False)

    # execute
    error: Optional[BaseException] = None
    try:
      await self.backend.pick_up_tips(
        ops=pickups, use_channels=use_channels, backend_params=backend_params
      )
    except BaseException as e:
      error = e

    # determine per-channel success
    successes = [error is None] * len(pickups)
    if error is not None:
      try:
        tip_presence = await self.backend.request_tip_presence()
        successes = [tip_presence[ch] is True for ch in use_channels]
      except Exception as tip_presence_error:
        if not isinstance(tip_presence_error, NotImplementedError):
          logger.warning("Failed to query tip presence after error: %s", tip_presence_error)
        if isinstance(error, ChannelizedError):
          successes = [ch not in error.errors for ch in use_channels]

    # commit or rollback
    for channel, op, success in zip(use_channels, pickups, successes):
      if does_tip_tracking() and not op.resource.tracker.is_disabled:
        (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
      (self.head[channel].commit if success else self.head[channel].rollback)()

    if error is not None:
      raise error

  @need_capability_ready
  async def drop_tips(
    self,
    tip_spots: Sequence[Union[TipSpot, Trash]],
    use_channels: Optional[List[int]] = None,
    offsets: Optional[List[Coordinate]] = None,
    allow_nonzero_volume: bool = False,
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips to tip spots or trash.

    Args:
      tip_spots: Tip spots or trash to drop to.
      use_channels: List of channels to use. If None, the first len(tip_spots) channels are used.
      offsets: List of offsets for each drop. Defaults to zero.
      allow_nonzero_volume: If True, drop even if the tip has liquid. Otherwise raise.
      backend_params: Vendor-specific parameters.

    Raises:
      NoTipError: If a channel does not have a tip.
      HasTipError: If a spot already has a tip.
    """

    not_valid = [ts for ts in tip_spots if not isinstance(ts, (TipSpot, Trash))]
    if not_valid:
      raise TypeError(f"Resources must be TipSpots or Trash, got {not_valid}")

    use_channels = use_channels or self._default_use_channels or list(range(len(tip_spots)))
    if len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels must be unique.")

    tips = []
    for channel in use_channels:
      tip = self.head[channel].get_tip()
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
        raise RuntimeError(f"Cannot drop tip with volume {tip.tracker.get_used_volume()}")
      tips.append(tip)

    offsets = offsets or [Coordinate.zero()] * len(tip_spots)

    self._make_sure_channels_exist(use_channels)
    if not (len(tip_spots) == len(offsets) == len(use_channels) == len(tips)):
      raise ValueError("Number of tip_spots, offsets, use_channels, and tips must be equal.")

    drops = [TipDrop(resource=ts, offset=o, tip=t) for ts, t, o in zip(tip_spots, tips, offsets)]

    # queue operations on trackers
    for channel, op in zip(use_channels, drops):
      if (
        does_tip_tracking()
        and isinstance(op.resource, TipSpot)
        and not op.resource.tracker.is_disabled
      ):
        op.resource.tracker.add_tip(op.tip, commit=False)
      self.head[channel].remove_tip()

    # execute
    error: Optional[BaseException] = None
    try:
      await self.backend.drop_tips(
        ops=drops, use_channels=use_channels, backend_params=backend_params
      )
    except BaseException as e:
      error = e

    # determine per-channel success
    successes = [error is None] * len(drops)
    if error is not None:
      try:
        tip_presence = await self.backend.request_tip_presence()
        successes = [tip_presence[ch] is False for ch in use_channels]
      except Exception as tip_presence_error:
        if not isinstance(tip_presence_error, NotImplementedError):
          logger.warning("Failed to query tip presence after error: %s", tip_presence_error)
        if isinstance(error, ChannelizedError):
          successes = [ch not in error.errors for ch in use_channels]

    # commit or rollback
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

  @need_capability_ready
  async def return_tips(
    self,
    use_channels: Optional[List[int]] = None,
    allow_nonzero_volume: bool = False,
    offsets: Optional[List[Coordinate]] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Return all tips currently picked up to their original place.

    Args:
      use_channels: Channels to return. If None, all channels with tips are used.
      allow_nonzero_volume: If True, return even if the tip has liquid.
      offsets: List of offsets for each drop.
      drop_backend_params: Vendor-specific parameters for the drop.

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

    await self.drop_tips(
      tip_spots=tip_spots,
      use_channels=channels,
      allow_nonzero_volume=allow_nonzero_volume,
      offsets=offsets,
      backend_params=drop_backend_params,
    )

  @need_capability_ready
  async def discard_tips(
    self,
    trash: Trash,
    use_channels: Optional[List[int]] = None,
    allow_nonzero_volume: bool = True,
    offsets: Optional[List[Coordinate]] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Permanently discard tips in the trash.

    Args:
      trash: The trash resource.
      use_channels: Channels to discard. If None, all channels with tips are used.
      allow_nonzero_volume: If True, discard even if the tip has liquid.
      offsets: List of offsets for each drop.
      drop_backend_params: Vendor-specific parameters for the drop.
    """

    if use_channels is None:
      use_channels = [c for c, t in self.head.items() if t.has_tip]

    n = len(use_channels)
    if n == 0:
      raise RuntimeError("No tips have been picked up and no channels were specified.")

    trash_offsets = get_tight_single_resource_liquid_op_offsets(trash, num_channels=n)
    offsets = [
      o + to if o is not None else to
      for o, to in zip(offsets or [None] * n, trash_offsets)  # type: ignore
    ]

    await self.drop_tips(
      tip_spots=[trash] * n,
      use_channels=use_channels,
      offsets=offsets,
      allow_nonzero_volume=allow_nonzero_volume,
      backend_params=drop_backend_params,
    )

  @need_capability_ready
  async def move_tips(
    self,
    source_tip_spots: List[TipSpot],
    dest_tip_spots: List[TipSpot],
    pick_up_backend_params: Optional[BackendParams] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Move tips from one tip rack to another.

    Examples:
      >>> await cap.move_tips(source_rack["A1":"A8"], dest_rack["B1":"B8"])
    """
    if len(source_tip_spots) != len(dest_tip_spots):
      raise ValueError("Number of source and destination tip spots must match.")

    use_channels = list(range(len(source_tip_spots)))
    await self.pick_up_tips(
      tip_spots=source_tip_spots,
      use_channels=use_channels,
      backend_params=pick_up_backend_params,
    )
    await self.drop_tips(
      tip_spots=dest_tip_spots,
      use_channels=use_channels,
      backend_params=drop_backend_params,
    )

  @need_capability_ready
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
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate liquid from the specified containers.

    Examples:
      Aspirate 50 uL from the first column:

      >>> await cap.aspirate(plate["A1:H1"], vols=[50]*8)

      Aspirate from a single container with multiple channels spread evenly:

      >>> await cap.aspirate([trough], vols=[50]*4, use_channels=[0,1,2,3])

    Args:
      resources: Containers to aspirate from. If a single resource is given with multiple channels,
        channels are spread across it according to `spread`.
      vols: Volume to aspirate per channel.
      use_channels: Channels to use. Defaults to 0..len(resources)-1.
      flow_rates: Flow rate per channel (ul/s). None = machine default.
      offsets: Offset per channel.
      liquid_height: Liquid height per channel (mm from bottom). None = machine default.
      blow_out_air_volume: Air volume to aspirate after liquid (ul). None = machine default.
      spread: How to space channels on a single resource: "wide", "tight", or "custom".
      mix: Mix parameters per channel.
      backend_params: Vendor-specific parameters.
    """

    not_containers = [r for r in resources if not isinstance(r, Container)]
    if not_containers:
      raise TypeError(f"Resources must be Containers, got {not_containers}")

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))
    if len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels must be unique.")

    offsets = offsets or [Coordinate.zero()] * len(use_channels)
    flow_rates = flow_rates or [None] * len(use_channels)
    liquid_height = liquid_height or [None] * len(use_channels)
    blow_out_air_volume = blow_out_air_volume or [None] * len(use_channels)

    vols = [float(v) for v in vols]
    flow_rates = [float(fr) if fr is not None else None for fr in flow_rates]
    liquid_height = [float(lh) if lh is not None else None for lh in liquid_height]
    blow_out_air_volume = [float(bav) if bav is not None else None for bav in blow_out_air_volume]

    self._blow_out_air_volume = blow_out_air_volume
    tips = [self.head[channel].get_tip() for channel in use_channels]

    for resource in resources:
      if isinstance(resource.parent, Plate) and resource.parent.has_lid():
        raise ValueError("Aspirating from a well with a lid is not supported.")

    self._make_sure_channels_exist(use_channels)
    for name, param in [
      ("resources", resources),
      ("vols", vols),
      ("offsets", offsets),
      ("flow_rates", flow_rates),
      ("liquid_height", liquid_height),
      ("blow_out_air_volume", blow_out_air_volume),
    ]:
      if len(param) != len(use_channels):
        raise ValueError(
          f"Length of {name} must match use_channels: {len(param)} != {len(use_channels)}"
        )

    # spread channels across a single resource
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
        raise ValueError("Invalid spread. Must be 'tight', 'wide', or 'custom'.")
      offsets = [c + o for c, o in zip(center_offsets, offsets)]

    aspirations = [
      Aspiration(
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

    # queue volume tracking
    for op in aspirations:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.remove_liquid(op.volume)
        op.tip.tracker.add_liquid(volume=op.volume)

    # execute
    error: Optional[Exception] = None
    try:
      await self.backend.aspirate(
        ops=aspirations, use_channels=use_channels, backend_params=backend_params
      )
    except Exception as e:
      error = e

    # determine per-channel success
    successes = [error is None] * len(aspirations)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [ch not in error.errors for ch in use_channels]

    # commit or rollback
    for channel, op, success in zip(use_channels, aspirations, successes):
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          (op.resource.tracker.commit if success else op.resource.tracker.rollback)()
        tip_volume_tracker = self.head[channel].get_tip().tracker
        (tip_volume_tracker.commit if success else tip_volume_tracker.rollback)()

    if error is not None:
      raise error

  @need_capability_ready
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
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense liquid to the specified containers.

    Examples:
      Dispense 50 uL to the first column:

      >>> await cap.dispense(plate["A1:H1"], vols=[50]*8)

    Args:
      resources: Containers to dispense to.
      vols: Volume to dispense per channel.
      use_channels: Channels to use. Defaults to 0..len(resources)-1.
      flow_rates: Flow rate per channel (ul/s). None = machine default.
      offsets: Offset per channel.
      liquid_height: Liquid height per channel (mm from bottom). None = machine default.
      blow_out_air_volume: Air volume to dispense after liquid (ul). None = machine default.
      spread: How to space channels on a single resource: "wide", "tight", or "custom".
      mix: Mix parameters per channel.
      backend_params: Vendor-specific parameters.
    """

    not_containers = [r for r in resources if not isinstance(r, Container)]
    if not_containers:
      raise TypeError(f"Resources must be Containers, got {not_containers}")

    use_channels = use_channels or self._default_use_channels or list(range(len(resources)))
    if len(set(use_channels)) != len(use_channels):
      raise ValueError("Channels must be unique.")

    offsets = offsets or [Coordinate.zero()] * len(use_channels)
    flow_rates = flow_rates or [None] * len(use_channels)
    liquid_height = liquid_height or [None] * len(use_channels)
    blow_out_air_volume = blow_out_air_volume or [None] * len(use_channels)

    vols = [float(v) for v in vols]
    flow_rates = [float(fr) if fr is not None else None for fr in flow_rates]
    liquid_height = [float(lh) if lh is not None else None for lh in liquid_height]
    blow_out_air_volume = [float(bav) if bav is not None else None for bav in blow_out_air_volume]

    # spread channels across a single resource
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
        raise ValueError("Invalid spread. Must be 'tight', 'wide', or 'custom'.")
      offsets = [c + o for c, o in zip(center_offsets, offsets)]

    tips = [self.head[channel].get_tip() for channel in use_channels]

    # check blow-out air volume against what was aspirated
    if does_volume_tracking():
      if any(bav is not None and bav != 0.0 for bav in blow_out_air_volume):
        if self._blow_out_air_volume is None:
          raise BlowOutVolumeError("No blowout volume was aspirated.")
        for requested_bav, done_bav in zip(blow_out_air_volume, self._blow_out_air_volume):
          if requested_bav is not None and done_bav is not None and requested_bav > done_bav:
            raise BlowOutVolumeError("Blowout volume is larger than aspirated volume")

    for resource in resources:
      if isinstance(resource.parent, Plate) and resource.parent.has_lid():
        raise ValueError("Dispensing to a well with a lid is not supported.")

    for name, param in [
      ("resources", resources),
      ("vols", vols),
      ("offsets", offsets),
      ("flow_rates", flow_rates),
      ("liquid_height", liquid_height),
      ("blow_out_air_volume", blow_out_air_volume),
    ]:
      if len(param) != len(use_channels):
        raise ValueError(
          f"Length of {name} must match use_channels: {len(param)} != {len(use_channels)}"
        )

    dispenses = [
      Dispense(
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

    # queue volume tracking
    for op in dispenses:
      if does_volume_tracking():
        if not op.resource.tracker.is_disabled:
          op.resource.tracker.add_liquid(volume=op.volume)
        op.tip.tracker.remove_liquid(op.volume)

    # execute
    error: Optional[Exception] = None
    try:
      await self.backend.dispense(
        ops=dispenses, use_channels=use_channels, backend_params=backend_params
      )
    except Exception as e:
      error = e

    # determine per-channel success
    successes = [error is None] * len(dispenses)
    if error is not None and isinstance(error, ChannelizedError):
      successes = [ch not in error.errors for ch in use_channels]

    # commit or rollback
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

  @need_capability_ready
  async def transfer(
    self,
    source: Well,
    targets: List[Well],
    source_vol: Optional[float] = None,
    ratios: Optional[List[float]] = None,
    target_vols: Optional[List[float]] = None,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rates: Optional[List[Optional[float]]] = None,
    aspirate_backend_params: Optional[BackendParams] = None,
    dispense_backend_params: Optional[BackendParams] = None,
  ):
    """Transfer liquid from one well to multiple targets.

    Examples:
      Transfer 50 uL from A1 to B1:

      >>> await cap.transfer(plate["A1"], plate["B1"], source_vol=50)

      Transfer 80 uL equally to the first column:

      >>> await cap.transfer(plate["A1"], plate["A1:H1"], source_vol=80)

      Transfer 60 uL in a 2:1 ratio:

      >>> await cap.transfer(plate["A1"], plate["B1:C1"], source_vol=60, ratios=[2, 1])

    Args:
      source: The source well.
      targets: The target wells.
      source_vol: The total volume to aspirate from source.
      ratios: Ratios for distributing liquid. If None, distribute equally.
      target_vols: Explicit volumes per target. Mutually exclusive with source_vol/ratios.
      aspiration_flow_rate: Flow rate for aspiration (ul/s).
      dispense_flow_rates: Flow rates for dispense per target (ul/s).
      aspirate_backend_params: Vendor-specific parameters for aspiration.
      dispense_backend_params: Vendor-specific parameters for dispense.
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
      backend_params=aspirate_backend_params,
    )
    dispense_flow_rates = dispense_flow_rates or [None] * len(targets)
    for target, vol, dfr in zip(targets, target_vols, dispense_flow_rates):
      await self.dispense(
        resources=[target],
        vols=[vol],
        flow_rates=[dfr],
        use_channels=[0],
        backend_params=dispense_backend_params,
      )

  @contextlib.contextmanager
  def use_channels(self, channels: List[int]) -> Generator[None, None, None]:
    """Temporarily use the specified channels as default for all operations.

    Examples:
      >>> with cap.use_channels([2]):
      ...   await cap.pick_up_tips(tip_rack["A1"])
      ...   await cap.aspirate(plate["A1"], vols=[50])
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
    trash: Trash,
    channels: Optional[List[int]] = None,
    discard: bool = True,
    pick_up_backend_params: Optional[BackendParams] = None,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Context manager that picks up tips on entry and discards/returns on exit.

    Examples:
      >>> async with cap.use_tips(tip_rack["A1":"H1"], trash=trash):
      ...   await cap.aspirate(plate["A1":"H1"], vols=[50]*8)
      ...   await cap.dispense(plate["A1":"H1"], vols=[50]*8)
    """
    if channels is None:
      channels = list(range(len(tip_spots)))
    if len(tip_spots) != len(channels):
      raise ValueError("Number of tip spots and channels must match.")

    await self.pick_up_tips(tip_spots, use_channels=channels, backend_params=pick_up_backend_params)
    try:
      yield
    finally:
      if discard:
        await self.discard_tips(
          trash=trash, use_channels=channels, drop_backend_params=drop_backend_params
        )
      else:
        await self.return_tips(use_channels=channels, drop_backend_params=drop_backend_params)
