"""Capability for 96-head liquid handling."""

import logging
from typing import Dict, List, Optional, Sequence, Union, cast

from pylabrobot.capabilities.capability import BackendParams, Capability, need_capability_ready
from pylabrobot.resources import (
  Container,
  Coordinate,
  Deck,
  Plate,
  Tip,
  TipRack,
  TipTracker,
  Trash,
  Well,
  does_tip_tracking,
  does_volume_tracking,
)

from .head96_backend import Head96Backend
from .standard import (
  DropTipRack,
  Mix,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)

logger = logging.getLogger(__name__)


class Head96(Capability):
  """96-head liquid handling: pick up tips, aspirate, dispense, drop tips.

  Faithfully ports the 96-head logic from the legacy LiquidHandler, including
  tip tracking with commit/rollback, volume tracking, partial tip pickup,
  single-container (trough) support, and convenience methods.

  See :doc:`/user_guide/capabilities/head96` for a walkthrough.
  """

  def __init__(
    self,
    backend: Head96Backend,
    deck: Deck,
    default_offset: Coordinate = Coordinate.zero(),
  ):
    super().__init__(backend=backend)
    self.backend: Head96Backend = backend
    self.head: Dict[int, TipTracker] = {}
    self.default_offset: Coordinate = default_offset
    self.deck = deck

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await super()._on_setup(backend_params=backend_params)
    self.head = {c: TipTracker(thing=f"96Head Channel {c}") for c in range(96)}

  def get_mounted_tips(self) -> List[Optional[Tip]]:
    """Get the tips currently mounted on the 96-head.

    Returns:
      A list of 96 tips, or None for channels without a tip.
    """
    return [tracker.get_tip() if tracker.has_tip else None for tracker in self.head.values()]

  def update_head_state(self, state: Dict[int, Optional[Tip]]):
    """Update the state of the 96-head.

    All keys must be valid channels (0-95). Channels not in `state` keep their current state.
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
    """Clear all tips from the 96-head."""
    self.update_head_state({c: None for c in self.head.keys()})

  def serialize_state(self) -> Dict:
    """Serialize the 96-head state for saving/restoring."""
    return {channel: tracker.serialize() for channel, tracker in self.head.items()}

  def load_state(self, state: Dict):
    """Load 96-head state from a serialized dict."""
    for channel, tracker_state in state.items():
      self.head[channel].load_state(tracker_state)

  def _get_origin_tip_rack(self) -> Optional[TipRack]:
    """Get the tip rack where the 96-head tips were picked up from.

    Returns None if no tips are mounted. Raises if tips are from different racks.
    """
    tip_spot = self.head[0].get_tip_origin()
    if tip_spot is None:
      return None
    tip_rack = tip_spot.parent
    if tip_rack is None:
      raise RuntimeError("No tip rack found for tip")
    for i in range(tip_rack.num_items):
      other_tip_spot = self.head[i].get_tip_origin()
      if other_tip_spot is None:
        raise RuntimeError("Not all channels have a tip origin")
      if other_tip_spot.parent != tip_rack:
        raise RuntimeError("All tips must be from the same tip rack")
    return tip_rack

  @staticmethod
  def _check_96_head_fits_in_container(container: Container) -> bool:
    """Check if the 96 head can fit in the given container."""
    tip_width = 2  # approximation
    distance_between_tips = 9
    return (
      container.get_absolute_size_x() >= tip_width + distance_between_tips * 11
      and container.get_absolute_size_y() >= tip_width + distance_between_tips * 7
    )

  @need_capability_ready
  async def pick_up_tips(
    self,
    tip_rack: TipRack,
    offset: Coordinate = Coordinate.zero(),
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips from a 96-tip rack.

    Not all tip spots need to have tips — only those with tips will be picked up.

    Examples:
      >>> await head96.pick_up_tips(my_tiprack)

    Args:
      tip_rack: The tip rack to pick up from. Must have 96 positions.
      offset: Additional offset (added to default_offset).
      backend_params: Vendor-specific parameters.
    """

    offset = self.default_offset + offset

    if not isinstance(tip_rack, TipRack):
      raise TypeError(f"Resource must be a TipRack, got {tip_rack}")
    if tip_rack.num_items != 96:
      raise ValueError("Tip rack must have 96 tips")

    # queue operation on all tip trackers
    tips: List[Optional[Tip]] = []
    for i, tip_spot in enumerate(tip_rack.get_all_items()):
      if not does_tip_tracking() and self.head[i].has_tip:
        self.head[i].remove_tip()
      # only add tips where one is present
      if tip_spot.has_tip():
        self.head[i].add_tip(tip_spot.get_tip(), origin=tip_spot, commit=False)
        tips.append(tip_spot.get_tip())
      else:
        tips.append(None)
      if does_tip_tracking() and not tip_spot.tracker.is_disabled and tip_spot.has_tip():
        tip_spot.tracker.remove_tip()

    pickup_operation = PickupTipRack(resource=tip_rack, offset=offset, tips=tips)
    try:
      await self.backend.pick_up_tips96(pickup=pickup_operation, backend_params=backend_params)
    except Exception as error:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.rollback()
        self.head[i].rollback()
      raise error
    else:
      for i, tip_spot in enumerate(tip_rack.get_all_items()):
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.commit()
        self.head[i].commit()

  @need_capability_ready
  async def drop_tips(
    self,
    resource: Union[TipRack, Trash],
    offset: Coordinate = Coordinate.zero(),
    allow_nonzero_volume: bool = False,
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips using the 96-head.

    Examples:
      >>> await head96.drop_tips(my_tiprack)
      >>> await head96.drop_tips(trash)

    Args:
      resource: The tip rack or trash to drop tips to.
      offset: Additional offset (added to default_offset).
      allow_nonzero_volume: If True, drop even if tips have liquid.
      backend_params: Vendor-specific parameters.
    """

    offset = self.default_offset + offset

    if not isinstance(resource, (TipRack, Trash)):
      raise TypeError(f"Resource must be a TipRack or Trash, got {resource}")
    if isinstance(resource, TipRack) and resource.num_items != 96:
      raise ValueError("Tip rack must have 96 tips")

    # queue operation on all tip trackers
    for i in range(96):
      if not self.head[i].has_tip:
        continue
      tip = self.head[i].get_tip()
      if tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume and does_volume_tracking():
        raise RuntimeError(
          f"Cannot drop tip with volume {tip.tracker.get_used_volume()} on channel {i}"
        )
      if isinstance(resource, TipRack):
        tip_spot = resource.get_item(i)
        if does_tip_tracking() and not tip_spot.tracker.is_disabled:
          tip_spot.tracker.add_tip(tip, commit=False)
      self.head[i].remove_tip()

    drop_operation = DropTipRack(resource=resource, offset=offset)
    try:
      await self.backend.drop_tips96(drop=drop_operation, backend_params=backend_params)
    except Exception as e:
      for i in range(96):
        if isinstance(resource, TipRack):
          tip_spot = resource.get_item(i)
          if does_tip_tracking() and not tip_spot.tracker.is_disabled:
            tip_spot.tracker.rollback()
        self.head[i].rollback()
      raise e
    else:
      for i in range(96):
        if isinstance(resource, TipRack):
          tip_spot = resource.get_item(i)
          if does_tip_tracking() and not tip_spot.tracker.is_disabled:
            tip_spot.tracker.commit()
        self.head[i].commit()

  @need_capability_ready
  async def return_tips(
    self,
    allow_nonzero_volume: bool = False,
    offset: Coordinate = Coordinate.zero(),
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Return the tips on the 96-head to the tip rack they were picked up from.

    Args:
      allow_nonzero_volume: If True, return even if tips have liquid.
      offset: Additional offset.
      drop_backend_params: Vendor-specific parameters for the drop.

    Raises:
      RuntimeError: If no tips have been picked up.
    """
    tip_rack = self._get_origin_tip_rack()
    if tip_rack is None:
      raise RuntimeError("No tips have been picked up with the 96 head")
    await self.drop_tips(
      tip_rack,
      allow_nonzero_volume=allow_nonzero_volume,
      offset=offset,
      backend_params=drop_backend_params,
    )

  @need_capability_ready
  async def discard_tips(
    self,
    trash: Optional[Trash] = None,
    allow_nonzero_volume: bool = True,
    drop_backend_params: Optional[BackendParams] = None,
  ):
    """Permanently discard tips from the 96-head into the trash.

    Args:
      trash: The trash resource. If None, automatically finds the 96-head trash on the deck.
      allow_nonzero_volume: If True, discard even if tips have liquid.
      drop_backend_params: Vendor-specific parameters for the drop.
    """
    if trash is None:
      if self.deck is None:
        raise ValueError("No trash provided and no deck set on Head96. Pass trash explicitly.")
      trash = self.deck.get_trash_area96()
    await self.drop_tips(
      trash, allow_nonzero_volume=allow_nonzero_volume, backend_params=drop_backend_params
    )

  @need_capability_ready
  async def aspirate(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    liquid_height: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    mix: Optional[Mix] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate from all wells in a plate or from a container.

    Examples:
      >>> await head96.aspirate(plate, volume=50)
      >>> await head96.aspirate(trough, volume=50)

    Args:
      resource: A Plate, Container, or list of 96 Wells.
      volume: Volume to aspirate per channel.
      offset: Additional offset (added to default_offset).
      flow_rate: Flow rate in ul/s. None = machine default.
      liquid_height: Liquid height in mm from bottom. None = machine default.
      blow_out_air_volume: Air volume to aspirate after liquid (ul).
      mix: Mix parameters.
      backend_params: Vendor-specific parameters.
    """

    offset = self.default_offset + offset

    if not (
      isinstance(resource, (Plate, Container))
      or (isinstance(resource, list) and all(isinstance(w, Well) for w in resource))
    ):
      raise TypeError(f"Resource must be a Plate, Container, or list of Wells, got {resource}")

    tips = [ch.get_tip() if ch.has_tip else None for ch in self.head.values()]

    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    # resolve resource to containers
    containers: Sequence[Container]
    if isinstance(resource, Plate):
      if resource.has_lid():
        raise ValueError("Aspirating from plate with lid")
      containers = resource.get_all_items() if resource.num_items > 1 else [resource.get_item(0)]
    elif isinstance(resource, Container):
      containers = [resource]
    elif isinstance(resource, list):
      containers = resource
    else:
      raise TypeError(f"Unexpected resource type: {type(resource)}")

    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]

    if len(containers) == 1:  # single container (trough)
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
    else:  # plate / list of wells
      plate = containers[0].parent
      for well in containers:
        if well.parent != plate:
          raise ValueError("All wells must be in the same plate")
      if len(containers) != 96:
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
      await self.backend.aspirate96(aspiration=aspiration, backend_params=backend_params)
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

  @need_capability_ready
  async def dispense(
    self,
    resource: Union[Plate, Container, List[Well]],
    volume: float,
    offset: Coordinate = Coordinate.zero(),
    flow_rate: Optional[float] = None,
    liquid_height: Optional[float] = None,
    blow_out_air_volume: Optional[float] = None,
    mix: Optional[Mix] = None,
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense to all wells in a plate or to a container.

    Examples:
      >>> await head96.dispense(plate, volume=50)

    Args:
      resource: A Plate, Container, or list of 96 Wells.
      volume: Volume to dispense per channel.
      offset: Additional offset (added to default_offset).
      flow_rate: Flow rate in ul/s. None = machine default.
      liquid_height: Liquid height in mm from bottom. None = machine default.
      blow_out_air_volume: Air volume to dispense after liquid (ul).
      mix: Mix parameters.
      backend_params: Vendor-specific parameters.
    """

    offset = self.default_offset + offset

    if not (
      isinstance(resource, (Plate, Container))
      or (isinstance(resource, list) and all(isinstance(w, Well) for w in resource))
    ):
      raise TypeError(f"Resource must be a Plate, Container, or list of Wells, got {resource}")

    tips = [ch.get_tip() if ch.has_tip else None for ch in self.head.values()]

    volume = float(volume)
    flow_rate = float(flow_rate) if flow_rate is not None else None
    blow_out_air_volume = float(blow_out_air_volume) if blow_out_air_volume is not None else None

    # resolve resource to containers
    containers: Sequence[Container]
    if isinstance(resource, Plate):
      if resource.has_lid():
        raise ValueError("Dispensing to plate with lid is not possible. Remove the lid first.")
      containers = resource.get_all_items() if resource.num_items > 1 else [resource.get_item(0)]
    elif isinstance(resource, Container):
      containers = [resource]
    elif isinstance(resource, list):
      containers = resource
    else:
      raise TypeError(f"Unexpected resource type: {type(resource)}")

    # remove liquid from tips
    for tip in tips:
      if tip is None:
        continue
      if does_volume_tracking():
        tip.tracker.remove_liquid(volume=volume)
      elif tip.tracker.get_used_volume() <= volume:
        tip.tracker.remove_liquid(volume=min(tip.tracker.get_used_volume(), volume))

    dispense_op: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]

    if len(containers) == 1:  # single container (trough)
      container = containers[0]
      if not self._check_96_head_fits_in_container(container):
        raise ValueError("Container too small to accommodate 96 head")

      if not container.tracker.is_disabled and does_volume_tracking():
        container.tracker.add_liquid(volume=len([t for t in tips if t is not None]) * volume)

      dispense_op = MultiHeadDispenseContainer(
        container=container,
        volume=volume,
        offset=offset,
        flow_rate=flow_rate,
        tips=tips,
        liquid_height=liquid_height,
        blow_out_air_volume=blow_out_air_volume,
        mix=mix,
      )
    else:  # plate / list of wells
      plate = containers[0].parent
      for well in containers:
        if well.parent != plate:
          raise ValueError("All wells must be in the same plate")
      if len(containers) != 96:
        raise ValueError(f"dispense96 expects 96 wells, got {len(containers)}")

      for well, tip in zip(containers, tips):
        if tip is None:
          continue
        if not well.tracker.is_disabled and does_volume_tracking():
          well.tracker.add_liquid(volume=volume)

      dispense_op = MultiHeadDispensePlate(
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
      await self.backend.dispense96(dispense=dispense_op, backend_params=backend_params)
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

  @need_capability_ready
  async def stamp(
    self,
    source: Plate,
    target: Plate,
    volume: float,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rate: Optional[float] = None,
    aspirate_backend_params: Optional[BackendParams] = None,
    dispense_backend_params: Optional[BackendParams] = None,
  ):
    """Stamp (aspirate and dispense) one plate onto another.

    Args:
      source: The source plate.
      target: The target plate.
      volume: The volume to transfer.
      aspiration_flow_rate: Flow rate for aspiration (ul/s).
      dispense_flow_rate: Flow rate for dispense (ul/s).
      aspirate_backend_params: Vendor-specific parameters for aspiration.
      dispense_backend_params: Vendor-specific parameters for dispense.
    """
    if (source.num_items_x, source.num_items_y) != (target.num_items_x, target.num_items_y):
      raise ValueError("Source and target plates must be the same shape")

    await self.aspirate(
      resource=source,
      volume=volume,
      flow_rate=aspiration_flow_rate,
      backend_params=aspirate_backend_params,
    )
    await self.dispense(
      resource=target,
      volume=volume,
      flow_rate=dispense_flow_rate,
      backend_params=dispense_backend_params,
    )
