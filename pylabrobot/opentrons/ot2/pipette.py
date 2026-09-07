from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterator, Optional

from pylabrobot.opentrons.types import Mount
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
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
  source: VolumeTracker, destination: VolumeTracker, volume: float
) -> Iterator[None]:
  """Track one liquid transfer, committing when its command succeeds."""
  trackers = [
    tracker
    for tracker in (source, destination)
    if does_volume_tracking() and not tracker.is_disabled
  ]
  try:
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


class OT2Pipette:
  """A pipette mounted on an OT-2 carriage.

  Instances are discovered and created by :meth:`OT2.setup`. Single-channel
  pipettes expose tip, liquid, and motion operations. Multi-channel pipettes are represented
  accurately, but their liquid operations are rejected until all eight tip and volume trackers
  can be updated atomically.
  """

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

    self.robot = robot
    self._run = robot._require_run()
    self.mount = mount
    self.name = name
    self.pipette_id = pipette_id
    self._spec = spec
    self._tip: Optional[Tip] = None
    self._tip_origin: Optional[TipSpot] = None

  @property
  def minimum_volume(self) -> float:
    """Minimum supported transfer volume, in µL."""
    return self._spec.minimum_volume

  @property
  def maximum_volume(self) -> float:
    """Maximum supported transfer volume, in µL."""
    return self._spec.maximum_volume

  @property
  def num_channels(self) -> int:
    """Number of nozzles on the pipette."""
    return self._spec.channels

  @property
  def has_tip(self) -> bool:
    """Whether the pipette holds a tip according to commands issued by this object."""
    return self._tip is not None

  @property
  def tip(self) -> Optional[Tip]:
    """The mounted tip, or ``None`` when no tip is mounted."""
    return self._tip

  def _require_active(self) -> None:
    if self.robot._require_run() is not self._run:
      raise RuntimeError("This pipette belongs to an earlier OT-2 run; use the current pipette")

  def _require_single_channel(self) -> None:
    if self.num_channels != 1:
      raise NotImplementedError(
        f"{self.name} has {self.num_channels} channels. Multi-channel liquid operations are not "
        "implemented yet."
      )

  def _require_tip(self) -> Tip:
    if self._tip is None:
      raise RuntimeError(f"The {self.mount} pipette does not have a tip")
    return self._tip

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

  async def _move_to(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    force_direct: bool = False,
  ) -> None:
    _require_finite_coordinate("location", location)
    if location.z < 0:
      raise ValueError("location.z must be non-negative")
    if not self.robot.geometry.can_reach_position(self.mount, location):
      bounds = self.robot.geometry.single_channel_reach(self.mount)
      raise ValueError(
        f"{location} is outside the {self.mount} mount's reachable x/y region {bounds}"
      )
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

  async def pick_up_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip from a tip rack and retract to at least traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._require_single_channel()
      if self._tip is not None:
        raise RuntimeError(f"The {self.mount} pipette already has a tip")
      if not isinstance(tip_spot.parent, TipRack):
        raise ValueError("tip_spot must be assigned to a tip rack")

      tip = tip_spot.get_tip()
      if not self.can_use_tip(tip):
        raise ValueError(f"{self.name} cannot use a {tip.maximal_volume:g} µL-capacity tip")
      offset = offset or Coordinate.zero()
      _require_finite_coordinate("offset", offset)
      tracked = does_tip_tracking() and not tip_spot.tracker.is_disabled
      if tracked:
        tip_spot.tracker.remove_tip(commit=False)

      try:
        await self.robot._load_tip_rack(tip_spot.parent, tip)
        binding = self.robot._require_labware().get(tip_spot.parent)
        await self._run.pick_up_tip(
          self.pipette_id,
          binding.labware_id,
          tip_spot.parent.get_child_identifier(tip_spot),
          offset + Coordinate(z=tip.total_tip_length),
        )
      except Exception:
        if tracked:
          tip_spot.tracker.rollback()
        raise

      if tracked:
        tip_spot.tracker.commit()
      self._tip = tip
      self._tip_origin = tip_spot
      await self._retract_to_traversal_height()

  async def drop_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop into a tip-rack position and retract vertically to at least traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      await self._drop_tip(tip_spot, offset, allow_nonzero_volume)

  async def _drop_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop a tip while the robot's operation lock is held."""
    self._require_single_channel()
    tip = self._require_tip()
    if not isinstance(tip_spot.parent, TipRack):
      raise ValueError("tip_spot must be assigned to a tip rack")
    if does_volume_tracking() and tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
      raise ValueError("The mounted tip still contains liquid")

    offset = offset or Coordinate.zero()
    _require_finite_coordinate("offset", offset)
    tracked = does_tip_tracking() and not tip_spot.tracker.is_disabled
    if tracked:
      tip_spot.tracker.add_tip(tip, origin=tip_spot, commit=False)

    try:
      await self.robot._load_tip_rack(tip_spot.parent, tip)
      binding = self.robot._require_labware().get(tip_spot.parent)
      await self._run.drop_tip(
        self.pipette_id,
        binding.labware_id,
        tip_spot.parent.get_child_identifier(tip_spot),
        offset + Coordinate(z=10),
      )
    except Exception:
      if tracked:
        tip_spot.tracker.rollback()
      raise

    if tracked:
      tip_spot.tracker.commit()
    self._tip = None
    self._tip_origin = None
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

  async def return_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Return the mounted tip to its pickup position and retract to at least traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      if self._tip_origin is None:
        raise RuntimeError("The mounted tip's origin is unknown")
      await self._drop_tip(
        self._tip_origin,
        offset=offset,
        allow_nonzero_volume=allow_nonzero_volume,
      )

  async def discard_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Discard into fixed trash and retract vertically to at least traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._require_single_channel()
      tip = self._require_tip()
      if does_volume_tracking() and tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
        raise ValueError("The mounted tip still contains liquid")
      offset = offset or Coordinate.zero()
      _require_finite_coordinate("offset", offset)

      await self._run.discard_tip_in_fixed_trash(self.pipette_id, offset + Coordinate(z=10))

      self._tip = None
      self._tip_origin = None
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

  async def aspirate(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Aspirate liquid from a container and return to traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._require_single_channel()
      tip = self._require_tip()
      volume = self._validate_volume(volume)
      flow_rate = self._spec.default_aspiration_flow_rate if flow_rate is None else float(flow_rate)
      if not math.isfinite(flow_rate) or flow_rate <= 0:
        raise ValueError("flow_rate must be finite and greater than zero")
      offset = offset or Coordinate.zero()
      location = self._liquid_location(container, offset, liquid_height)

      with _track_liquid_transfer(container.tracker, tip.tracker, volume):
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._run.aspirate_in_place(self.pipette_id, volume, flow_rate)
      await self._retract_to_traversal_height()

  async def dispense(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Dispense liquid into a container and return to traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._require_single_channel()
      tip = self._require_tip()
      volume = self._validate_volume(volume)
      flow_rate = self._spec.default_dispense_flow_rate if flow_rate is None else float(flow_rate)
      if not math.isfinite(flow_rate) or flow_rate <= 0:
        raise ValueError("flow_rate must be finite and greater than zero")
      offset = offset or Coordinate.zero()
      location = self._liquid_location(container, offset, liquid_height)

      with _track_liquid_transfer(tip.tracker, container.tracker, volume):
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._run.dispense_in_place(self.pipette_id, volume, flow_rate)
      await self._retract_to_traversal_height()

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
    """Mix in place using aspiration and dispense cycles, then retract to traversal height."""
    async with self.robot._operation_lock:
      self._require_active()
      self._require_single_channel()
      tip = self._require_tip()
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
      location = self._liquid_location(container, offset, liquid_height)
      for repetition in range(repetitions):
        with _track_liquid_transfer(container.tracker, tip.tracker, volume):
          if repetition == 0:
            await self._move_to(location, minimum_z_height=self.robot.traversal_height)
          await self._run.aspirate_in_place(self.pipette_id, volume, aspiration_flow_rate)
        with _track_liquid_transfer(tip.tracker, container.tracker, volume):
          await self._run.dispense_in_place(self.pipette_id, volume, dispense_flow_rate)
      await self._retract_to_traversal_height()
