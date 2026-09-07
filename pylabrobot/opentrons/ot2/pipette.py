from __future__ import annotations

import math
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterator, Optional, Sequence, Tuple

from pylabrobot.opentrons.types import Mount
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.tip_tracker import does_tip_tracking
from pylabrobot.resources.volume_tracker import VolumeTracker, does_volume_tracking

if TYPE_CHECKING:
  from pylabrobot.opentrons.ot2.ot2 import OT2


@dataclass(frozen=True)
class _PipetteSpec:
  minimum_volume: float
  maximum_volume: float
  channels: int
  default_aspiration_flow_rate: float
  default_dispense_flow_rate: float


_PIPETTE_SPECS = {
  "p10_single": _PipetteSpec(1, 10, 1, 5, 10),
  "p10_multi": _PipetteSpec(1, 10, 8, 5, 10),
  "p20_single_gen2": _PipetteSpec(1, 20, 1, 3.78, 7.56),
  "p20_multi_gen2": _PipetteSpec(1, 20, 8, 7.6, 7.6),
  "p50_single": _PipetteSpec(5, 50, 1, 25, 50),
  "p50_multi": _PipetteSpec(5, 50, 8, 25, 50),
  "p300_single": _PipetteSpec(30, 300, 1, 150, 300),
  "p300_multi": _PipetteSpec(30, 300, 8, 150, 300),
  "p300_single_gen2": _PipetteSpec(20, 300, 1, 46.43, 92.86),
  "p300_multi_gen2": _PipetteSpec(20, 300, 8, 94, 94),
  "p1000_single": _PipetteSpec(100, 1000, 1, 500, 1000),
  "p1000_single_gen2": _PipetteSpec(100, 1000, 1, 137.35, 274.7),
}


_COMPATIBLE_TIP_CAPACITIES: Dict[float, set] = {
  10: {10},
  20: {10, 20},
  50: {200},
  300: {200, 300},
  1000: {1000},
}


def _require_finite_coordinate(name: str, coordinate: Coordinate) -> None:
  if not all(math.isfinite(axis) for axis in coordinate):
    raise ValueError(f"{name} coordinates must be finite")


@contextmanager
def _track_liquid_transfer(
  sources: Sequence[VolumeTracker], destinations: Sequence[VolumeTracker], volume: float
) -> Iterator[None]:
  """Stage all nozzle transfers and commit together when their command succeeds."""
  trackers = [
    tracker
    for tracker in (*sources, *destinations)
    if does_volume_tracking() and not tracker.is_disabled
  ]
  try:
    for source, destination in zip(sources, destinations):
      if source in trackers:
        source.remove_liquid(volume)
      if destination in trackers:
        destination.add_liquid(volume)
    yield
  except BaseException:
    for tracker in trackers:
      tracker.rollback()
    raise
  else:
    for tracker in trackers:
      tracker.commit()


class _OT2Pipette(ABC):
  """Shared motion, command sequencing, and transactional tracking for mounted OT-2 pipettes."""

  def __init__(
    self,
    robot: OT2,
    mount: Mount,
    name: str,
    pipette_id: str,
  ):
    try:
      spec = _PIPETTE_SPECS[name]
    except KeyError as error:
      raise ValueError(f"Unsupported OT-2 pipette {name!r}") from error

    if spec.channels != self.num_channels:
      raise ValueError(f"{name} requires a {spec.channels}-channel pipette class")

    self.robot = robot
    self._run = robot._require_run()
    self.mount = mount
    self.name = name
    self.pipette_id = pipette_id
    self._spec = spec
    self._tips: Tuple[Tip, ...] = ()
    self._tip_origins: Tuple[TipSpot, ...] = ()

  @property
  def minimum_volume(self) -> float:
    """Minimum supported transfer volume, in µL."""
    return self._spec.minimum_volume

  @property
  def maximum_volume(self) -> float:
    """Maximum supported transfer volume, in µL."""
    return self._spec.maximum_volume

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """Number of nozzles exposed by this pipette class."""
    ...

  @property
  def has_tip(self) -> bool:
    """Whether the pipette holds a tip according to commands issued by this object."""
    return bool(self._tips)

  def _require_active(self) -> None:
    if self.robot._require_run() is not self._run:
      raise RuntimeError("This pipette belongs to an earlier OT-2 run; use the current pipette")

  def _require_tips(self) -> Tuple[Tip, ...]:
    """Return the mounted tips, requiring a tip on every nozzle."""
    if not self._tips:
      raise RuntimeError(f"The {self.mount} pipette does not have a tip")
    return self._tips

  def _validate_targets(self, targets: Sequence[Resource]) -> None:
    """Require one target per nozzle."""
    if len(targets) != self.num_channels:
      raise ValueError(f"{self.name} requires {self.num_channels} targets in nozzle order")

  def _validate_nozzle_positions(self, positions: Sequence[Coordinate]) -> None:
    """Require finite target coordinates."""
    for position in positions:
      _require_finite_coordinate("target", position)

  def _tip_rack(self, tip_spots: Sequence[TipSpot]) -> TipRack:
    """Validate tip spots and return its rack."""
    self._validate_targets(tip_spots)
    rack = tip_spots[0].parent
    if not isinstance(rack, TipRack):
      raise ValueError("tip spots must be assigned to a tip rack")
    if self.robot.deck.get_slot(rack) is None:
      raise ValueError("tip rack must be assigned directly to an OT-2 deck slot")
    return rack

  def _validate_volume(self, volume: float) -> float:
    volume = float(volume)
    if not self.minimum_volume <= volume <= self.maximum_volume:
      raise ValueError(
        f"volume must be between {self.minimum_volume:g} and {self.maximum_volume:g} µL "
        f"for {self.name}"
      )
    return volume

  def can_use_tip(self, tip: Tip) -> bool:
    """Whether the tip capacity is supported by this pipette."""
    return tip.maximal_volume in _COMPATIBLE_TIP_CAPACITIES[self.maximum_volume]

  def _validate_position(self, location: Coordinate) -> None:
    """Check the reference nozzle target in the robot frame before staging an operation."""
    _require_finite_coordinate("location", location)
    if location.z < 0:
      raise ValueError("location.z must be non-negative")
    if not self.robot.geometry.can_reach_position(self.mount, location):
      bounds = self.robot.geometry.single_channel_reach(self.mount)
      raise ValueError(
        f"{location} is outside the {self.mount} mount's reachable x/y region {bounds}"
      )

  async def _move_to(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    force_direct: bool = False,
  ) -> None:
    self._validate_position(location)
    if speed is not None and (not math.isfinite(speed) or speed <= 0):
      raise ValueError("speed must be finite and greater than zero")
    if minimum_z_height is not None and (
      not math.isfinite(minimum_z_height) or minimum_z_height < 0
    ):
      raise ValueError("minimum_z_height must be finite and non-negative")

    await self._run.move_to(
      self.pipette_id,
      location,
      speed=speed,
      minimum_z_height=minimum_z_height,
      force_direct=force_direct,
    )

  async def move_to(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    force_direct: bool = False,
  ) -> None:
    """Move to an absolute robot-frame coordinate, then retract to at least traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      await self._move_to(
        location=location,
        speed=speed,
        minimum_z_height=minimum_z_height,
        force_direct=force_direct,
      )
      await self._retract_to_traversal_height()

  def _tip_command_location(self, tip_spot: TipSpot, offset: Coordinate) -> Coordinate:
    """Convert a tip command's well-bottom offset to the reference nozzle's robot position."""
    return self.robot._deck_to_robot_frame(
      tip_spot.get_location_wrt(self.robot.deck, "c", "c", "b") + offset
    )

  def _check_tip_pickup(self, tip_spots: Sequence[TipSpot], tip: Tip, offset: Coordinate) -> None:
    """Check the pickup target before loading labware or changing tip trackers."""
    self._validate_position(
      self._tip_command_location(tip_spots[0], offset + Coordinate(z=tip.total_tip_length))
    )

  async def _pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip per nozzle with one command, then retract to traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      if self.has_tip:
        raise RuntimeError(f"The {self.mount} pipette already has a tip")
      tip_spots = tuple(tip_spots)
      rack = self._tip_rack(tip_spots)
      tips = tuple(spot.get_tip() for spot in tip_spots)
      tip = tips[0]
      if not self.can_use_tip(tip):
        raise ValueError(f"{self.name} cannot use a {tip.maximal_volume:g} µL-capacity tip")
      if any(other != tip for other in tips):
        raise ValueError("All nozzles must use the same tip type")
      offset = offset or Coordinate.zero()
      _require_finite_coordinate("offset", offset)
      self._check_tip_pickup(tip_spots, tip, offset)
      tracked = [
        spot.tracker for spot in tip_spots if does_tip_tracking() and not spot.tracker.is_disabled
      ]
      try:
        for tracker in tracked:
          tracker.remove_tip(commit=False)
        await self.robot._load_tip_rack(rack, tip)
        binding = self.robot._require_labware().get(rack)
        await self._run.pick_up_tip(
          self.pipette_id,
          binding.labware_id,
          rack.get_child_identifier(tip_spots[0]),
          offset + Coordinate(z=tip.total_tip_length),
        )
      except BaseException:
        for tracker in tracked:
          tracker.rollback()
        raise

      for tracker in tracked:
        tracker.commit()
      self._tips = tips
      self._tip_origins = tip_spots
      await self._retract_to_traversal_height()

  async def _drop_tips(
    self,
    tip_spots: Sequence[TipSpot],
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop all mounted tips into a rack with one command, then retract."""
    async with self.robot._operation_lock:
      self._require_active()
      await self._drop_tips_locked(tuple(tip_spots), offset, allow_nonzero_volume)

  def _check_tip_volumes(self, allow_nonzero_volume: bool) -> None:
    """Require mounted tips and reject disposal of liquid unless explicitly allowed."""
    tips = self._require_tips()
    if does_volume_tracking() and not allow_nonzero_volume:
      if any(tip.tracker.get_used_volume() > 0 for tip in tips):
        raise ValueError("A mounted tip still contains liquid")

  async def _drop_tips_locked(
    self,
    tip_spots: Sequence[TipSpot],
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop tips while the robot's operation lock is held."""
    self._check_tip_volumes(allow_nonzero_volume)
    rack = self._tip_rack(tip_spots)
    tip = self._tips[0]
    offset = offset or Coordinate.zero()
    _require_finite_coordinate("offset", offset)
    self._validate_position(self._tip_command_location(tip_spots[0], offset + Coordinate(z=10)))
    tracked = [
      spot.tracker for spot in tip_spots if does_tip_tracking() and not spot.tracker.is_disabled
    ]
    try:
      for spot, mounted_tip in zip(tip_spots, self._tips):
        if spot.tracker in tracked:
          spot.tracker.add_tip(mounted_tip, origin=spot, commit=False)
      await self.robot._load_tip_rack(rack, tip)
      binding = self.robot._require_labware().get(rack)
      await self._run.drop_tip(
        self.pipette_id,
        binding.labware_id,
        rack.get_child_identifier(tip_spots[0]),
        offset + Coordinate(z=10),
      )
    except BaseException:
      for tracker in tracked:
        tracker.rollback()
      raise

    for tracker in tracked:
      tracker.commit()
    self._tips = ()
    self._tip_origins = ()
    await self._retract_to_traversal_height()

  async def _retract_to_traversal_height(self) -> None:
    """Raise the nozzle or mounted tip from its reported position under the operation lock."""
    position = await self._run.get_position(self.pipette_id)
    _require_finite_coordinate("position", position)
    if position.z < self.robot.traversal_height:
      await self._move_to(
        Coordinate(position.x, position.y, self.robot.traversal_height),
        force_direct=True,
      )

  async def _return_tips(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Return all mounted tips to their pickup positions, then retract."""
    async with self.robot._operation_lock:
      self._require_active()
      if not self._tip_origins:
        raise RuntimeError("The mounted tip's origin is unknown")
      await self._drop_tips_locked(self._tip_origins, offset, allow_nonzero_volume)

  async def _discard_tips(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Discard all mounted tips into fixed trash and retract to traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._check_tip_volumes(allow_nonzero_volume)
      offset = offset or Coordinate.zero()
      _require_finite_coordinate("offset", offset)
      await self._run.discard_tip_in_fixed_trash(self.pipette_id, offset + Coordinate(z=10))
      self._tips = ()
      self._tip_origins = ()
      await self._retract_to_traversal_height()

  def _liquid_location(
    self,
    container: Container,
    offset: Coordinate,
    liquid_height: float,
  ) -> Coordinate:
    _require_finite_coordinate("offset", offset)
    if not math.isfinite(liquid_height) or liquid_height < 0:
      raise ValueError("liquid_height must be finite and non-negative")
    location = container.get_location_wrt(
      self.robot.deck,
      "c",
      "c",
      "cavity_bottom",
    )
    return self.robot._deck_to_robot_frame(location + offset + Coordinate(z=liquid_height))

  def _liquid_targets(
    self,
    containers: Sequence[Container],
    offset: Coordinate,
    liquid_height: float,
  ) -> Tuple[Tuple[VolumeTracker, ...], Coordinate]:
    """Resolve one container per nozzle and the reference nozzle's liquid position."""
    self._validate_targets(containers)
    locations = [self._liquid_location(c, offset, liquid_height) for c in containers]
    self._validate_nozzle_positions(locations)
    self._validate_position(locations[0])
    return tuple(c.tracker for c in containers), locations[0]

  async def _aspirate(
    self,
    containers: Sequence[Container],
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Aspirate ``volume`` per nozzle from one container or a full column, then retract."""
    async with self.robot._operation_lock:
      self._require_active()
      tips = self._require_tips()
      tip_trackers = tuple(tip.tracker for tip in tips)
      volume = self._validate_volume(volume)
      flow_rate = self._spec.default_aspiration_flow_rate if flow_rate is None else float(flow_rate)
      if not math.isfinite(flow_rate) or flow_rate <= 0:
        raise ValueError("flow_rate must be finite and greater than zero")
      offset = offset or Coordinate.zero()
      container_trackers, location = self._liquid_targets(containers, offset, liquid_height)

      with _track_liquid_transfer(container_trackers, tip_trackers, volume):
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._run.aspirate_in_place(self.pipette_id, volume, flow_rate)
      await self._retract_to_traversal_height()

  async def _dispense(
    self,
    containers: Sequence[Container],
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Dispense ``volume`` per nozzle into one container or a full column, then retract."""
    async with self.robot._operation_lock:
      self._require_active()
      tips = self._require_tips()
      tip_trackers = tuple(tip.tracker for tip in tips)
      volume = self._validate_volume(volume)
      flow_rate = self._spec.default_dispense_flow_rate if flow_rate is None else float(flow_rate)
      if not math.isfinite(flow_rate) or flow_rate <= 0:
        raise ValueError("flow_rate must be finite and greater than zero")
      offset = offset or Coordinate.zero()
      container_trackers, location = self._liquid_targets(containers, offset, liquid_height)

      with _track_liquid_transfer(tip_trackers, container_trackers, volume):
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._run.dispense_in_place(self.pipette_id, volume, flow_rate)
      await self._retract_to_traversal_height()

  async def _mix(
    self,
    containers: Sequence[Container],
    volume: float,
    repetitions: int,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Mix ``volume`` per nozzle in one container or a full column, then retract."""
    async with self.robot._operation_lock:
      self._require_active()
      tips = self._require_tips()
      tip_trackers = tuple(tip.tracker for tip in tips)
      volume = self._validate_volume(volume)
      if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
      aspiration_flow_rate = (
        self._spec.default_aspiration_flow_rate
        if aspiration_flow_rate is None
        else float(aspiration_flow_rate)
      )
      dispense_flow_rate = (
        self._spec.default_dispense_flow_rate
        if dispense_flow_rate is None
        else float(dispense_flow_rate)
      )
      if (
        not math.isfinite(aspiration_flow_rate)
        or not math.isfinite(dispense_flow_rate)
        or aspiration_flow_rate <= 0
        or dispense_flow_rate <= 0
      ):
        raise ValueError("flow rates must be finite and greater than zero")

      offset = offset or Coordinate.zero()
      container_trackers, location = self._liquid_targets(containers, offset, liquid_height)
      for repetition in range(repetitions):
        with _track_liquid_transfer(container_trackers, tip_trackers, volume):
          if repetition == 0:
            await self._move_to(location, minimum_z_height=self.robot.traversal_height)
          await self._run.aspirate_in_place(self.pipette_id, volume, aspiration_flow_rate)
        with _track_liquid_transfer(tip_trackers, container_trackers, volume):
          await self._run.dispense_in_place(self.pipette_id, volume, dispense_flow_rate)
      await self._retract_to_traversal_height()


class OT2SingleChannelPipette(_OT2Pipette):
  """A single-channel OT-2 pipette, discovered and created by :meth:`OT2.setup`."""

  @property
  def num_channels(self) -> int:
    """One independently addressed nozzle."""
    return 1

  @property
  def tip(self) -> Optional[Tip]:
    """The single-channel mounted tip, or ``None`` when no tip is mounted."""
    return self._tips[0] if self._tips else None

  async def pick_up_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip from a tip rack and retract to at least traversal height."""
    await self._pick_up_tips([tip_spot], offset)

  async def drop_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop into a tip-rack position and retract vertically to at least traversal height."""
    await self._drop_tips([tip_spot], offset, allow_nonzero_volume)

  async def return_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Return the mounted single tip to its pickup position, then retract."""
    await self._return_tips(offset, allow_nonzero_volume)

  async def discard_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Discard a single tip into fixed trash and retract to traversal height."""
    await self._discard_tips(offset, allow_nonzero_volume)

  async def aspirate(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Aspirate ``volume`` from a container, then retract."""
    await self._aspirate([container], volume, flow_rate, liquid_height, offset)

  async def dispense(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Dispense ``volume`` into a container, then retract."""
    await self._dispense([container], volume, flow_rate, liquid_height, offset)

  async def mix(
    self,
    container: Container,
    volume: float,
    repetitions: int,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Mix ``volume`` in a container, then retract."""
    await self._mix(
      [container],
      volume,
      repetitions,
      aspiration_flow_rate,
      dispense_flow_rate,
      liquid_height,
      offset,
    )


class OT2_8ChannelPipette(_OT2Pipette):
  """An eight-channel OT-2 pipette with a rigid, full-column nozzle layout.

  Instances are discovered and created by :meth:`OT2.setup`. Targets run from the
  back nozzle to the front (rows A-H). Volume, flow rate, liquid height, and offset
  apply equally to every nozzle. Partial columns are not supported.
  """

  @property
  def num_channels(self) -> int:
    """Eight nozzles at 9 mm pitch."""
    return 8

  @property
  def tips(self) -> Tuple[Tip, ...]:
    """Mounted tips in nozzle order, or an empty tuple when no tips are mounted."""
    return self._tips

  def _validate_targets(self, targets: Sequence[Resource]) -> None:
    """Require one distinct target per nozzle on the same resource."""
    super()._validate_targets(targets)
    if len({id(target) for target in targets}) != len(targets):
      raise ValueError("Each nozzle must address a distinct target")
    if any(target.parent is not targets[0].parent for target in targets):
      raise ValueError("All targets must belong to the same resource")

  def _validate_nozzle_positions(self, positions: Sequence[Coordinate]) -> None:
    """Require targets to align with the rigid head's 9 mm back-to-front pitch."""
    super()._validate_nozzle_positions(positions)
    for nozzle, position in enumerate(positions):
      expected = positions[0] + Coordinate(y=-9 * nozzle)
      if any(abs(actual - target) > 0.01 for actual, target in zip(position, expected)):
        raise ValueError("Targets must align in nozzle order at 9 mm pitch and equal height")

  def _tip_rack(self, tip_spots: Sequence[TipSpot]) -> TipRack:
    """Validate a complete A-H column and return its rack."""
    rack = super()._tip_rack(tip_spots)
    column = rack.get_child_identifier(tip_spots[0])[1:]
    if rack.num_items_y != 8 or [rack.get_child_identifier(spot) for spot in tip_spots] != [
      f"{row}{column}" for row in "ABCDEFGH"
    ]:
      raise ValueError("Eight-channel tip operations require a full A-H rack column")
    self._validate_nozzle_positions(
      [spot.get_location_wrt(self.robot.deck, "c", "c", "b") for spot in tip_spots]
    )
    return rack

  def _check_tip_pickup(self, tip_spots: Sequence[TipSpot], tip: Tip, offset: Coordinate) -> None:
    """Reject labware overlapping the pickup footprint near the bare nozzle height.

    The full nozzle span is padded by 5 mm in XY and 10 mm vertically. This is a
    conservative destination check, not a swept-path or complete pipette-body model.
    """
    super()._check_tip_pickup(tip_spots, tip, offset)
    primary = tip_spots[0].get_location_wrt(self.robot.deck, "c", "c", "b") + offset
    nozzle_z = primary.z + tip.total_tip_length - tip.fitting_depth
    x_min, x_max = primary.x - 5, primary.x + 5
    y_min, y_max = primary.y - 9 * (self.num_channels - 1) - 5, primary.y + 5
    for labware in self.robot.deck.slots:
      if labware is None or labware is tip_spots[0].parent:
        continue
      for resource in [labware, *labware.get_all_children()]:
        corners = [
          resource.get_location_wrt(self.robot.deck, x, y, z)
          for x in ("l", "r")
          for y in ("f", "b")
          for z in ("b", "t")
        ]
        for corner in corners:
          _require_finite_coordinate("surrounding resource", corner)
        if (
          max(c.x for c in corners) >= x_min
          and min(c.x for c in corners) <= x_max
          and max(c.y for c in corners) >= y_min
          and min(c.y for c in corners) <= y_max
          and max(c.z for c in corners) >= nozzle_z - 10
        ):
          raise ValueError(f"Eight-channel pickup footprint overlaps resource {resource.name!r}")

  async def pick_up_tips(
    self,
    tip_spots: Sequence[TipSpot],
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip per nozzle with one command, then retract to traversal height."""
    await self._pick_up_tips(tip_spots, offset)

  async def drop_tips(
    self,
    tip_spots: Sequence[TipSpot],
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop all mounted tips into a rack with one command, then retract."""
    await self._drop_tips(tip_spots, offset, allow_nonzero_volume)

  async def return_tips(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Return all mounted tips to their pickup positions, then retract."""
    await self._return_tips(offset, allow_nonzero_volume)

  async def discard_tips(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Discard all mounted tips into fixed trash and retract to traversal height."""
    await self._discard_tips(offset, allow_nonzero_volume)

  async def aspirate(
    self,
    containers: Sequence[Container],
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Aspirate ``volume`` per nozzle from a full column, then retract."""
    await self._aspirate(tuple(containers), volume, flow_rate, liquid_height, offset)

  async def dispense(
    self,
    containers: Sequence[Container],
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Dispense ``volume`` per nozzle into a full column, then retract."""
    await self._dispense(tuple(containers), volume, flow_rate, liquid_height, offset)

  async def mix(
    self,
    containers: Sequence[Container],
    volume: float,
    repetitions: int,
    aspiration_flow_rate: Optional[float] = None,
    dispense_flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Mix ``volume`` per nozzle in a full column, then retract."""
    await self._mix(
      tuple(containers),
      volume,
      repetitions,
      aspiration_flow_rate,
      dispense_flow_rate,
      liquid_height,
      offset,
    )
