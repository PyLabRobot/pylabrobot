from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot import utils
from pylabrobot.io.http import HTTP
from pylabrobot.resources.container import Container
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons import OT2RobotGeometry, OTDeck
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.tip_tracker import does_tip_tracking
from pylabrobot.resources.volume_tracker import does_volume_tracking

logger = logging.getLogger(__name__)

Mount = Literal["left", "right"]

_OT_DECK_IS_ADDRESSABLE_AREA_VERSION = "7.1.0"


class OpentronsOT2Error(RuntimeError):
  """An error reported by the OT-2 HTTP API."""


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

_OFFICIAL_TIP_RACKS = {
  "Opentrons OT-2 96 Filter Tip Rack 10 µL": "opentrons_96_filtertiprack_10ul",
  "Opentrons OT-2 96 Filter Tip Rack 20 µL": "opentrons_96_filtertiprack_20ul",
  "Opentrons OT-2 96 Filter Tip Rack 200 µL": "opentrons_96_filtertiprack_200ul",
  "Opentrons OT-2 96 Filter Tip Rack 1000 µL": "opentrons_96_filtertiprack_1000ul",
  "Opentrons OT-2 96 Tip Rack 10 µL": "opentrons_96_tiprack_10ul",
  "Opentrons OT-2 96 Tip Rack 20 µL": "opentrons_96_tiprack_20ul",
  "Opentrons OT-2 96 Tip Rack 300 µL": "opentrons_96_tiprack_300ul",
  "Opentrons OT-2 96 Tip Rack 1000 µL": "opentrons_96_tiprack_1000ul",
}


def _version_tuple(version: str) -> Tuple[int, ...]:
  parts = []
  for part in version.split("."):
    match = re.match(r"\d+", part)
    if match is None:
      break
    parts.append(int(match.group()))
  return tuple(parts)


def _version_at_least(version: str, required: str) -> bool:
  actual = _version_tuple(version)
  minimum = _version_tuple(required)
  width = max(len(actual), len(minimum))
  return actual + (0,) * (width - len(actual)) >= minimum + (0,) * (width - len(minimum))


def _require_finite_coordinate(name: str, coordinate: Coordinate) -> None:
  if not all(math.isfinite(axis) for axis in coordinate):
    raise ValueError(f"{name} coordinates must be finite")


class OT2Pipette:
  """A pipette mounted on an OT-2 carriage.

  Instances are discovered and created by :meth:`OpentronsOT2.setup`. Single-channel
  pipettes expose tip, liquid, and motion operations. Multi-channel pipettes are represented
  accurately, but their liquid operations are rejected until all eight tip and volume trackers
  can be updated atomically.
  """

  def __init__(
    self,
    robot: OpentronsOT2,
    mount: Mount,
    name: str,
    pipette_id: str,
  ):
    try:
      spec = _PIPETTE_SPECS[name]
    except KeyError as error:
      raise ValueError(f"Unsupported OT-2 pipette {name!r}") from error

    self.robot = robot
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
  def channels(self) -> int:
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

  def _require_single_channel(self) -> None:
    if self.channels != 1:
      raise NotImplementedError(
        f"{self.name} has {self.channels} channels. Multi-channel liquid operations are not "
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

    params: Dict[str, Any] = {
      "pipetteId": self.pipette_id,
      "coordinates": {"x": location.x, "y": location.y, "z": location.z},
      "forceDirect": force_direct,
    }
    if minimum_z_height is not None:
      params["minimumZHeight"] = minimum_z_height
    if speed is not None:
      params["speed"] = speed
    await self.robot._enqueue_command("moveToCoordinates", params)

  async def move_to(
    self,
    location: Coordinate,
    speed: Optional[float] = None,
    minimum_z_height: Optional[float] = None,
    force_direct: bool = False,
  ) -> None:
    """Move the pipette's nozzle or mounted tip to an absolute robot-frame coordinate."""
    async with self.robot._operation_lock:
      await self._move_to(
        location=location,
        speed=speed,
        minimum_z_height=minimum_z_height,
        force_direct=force_direct,
      )

  async def pick_up_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Pick up one tip from a tip rack."""
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
      async with self.robot._operation_lock:
        await self.robot._assign_tip_rack(tip_spot.parent, tip)
        await self.robot._enqueue_command(
          "pickUpTip",
          {
            "labwareId": self.robot._ot_name(tip_spot.parent.name),
            "wellName": self.robot._well_name(tip_spot),
            "wellLocation": {
              "origin": "bottom",
              "offset": {
                "x": offset.x,
                "y": offset.y,
                "z": offset.z + tip.total_tip_length,
              },
            },
            "pipetteId": self.pipette_id,
          },
        )
    except Exception:
      if tracked:
        tip_spot.tracker.rollback()
      raise

    if tracked:
      tip_spot.tracker.commit()
    self._tip = tip
    self._tip_origin = tip_spot

  async def drop_tip(
    self,
    tip_spot: TipSpot,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Drop the mounted tip into a tip-rack position."""
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
      async with self.robot._operation_lock:
        await self.robot._assign_tip_rack(tip_spot.parent, tip)
        await self.robot._enqueue_command(
          "dropTip",
          {
            "labwareId": self.robot._ot_name(tip_spot.parent.name),
            "wellName": self.robot._well_name(tip_spot),
            "wellLocation": {
              "origin": "bottom",
              "offset": {"x": offset.x, "y": offset.y, "z": offset.z + 10},
            },
            "pipetteId": self.pipette_id,
          },
        )
    except Exception:
      if tracked:
        tip_spot.tracker.rollback()
      raise

    if tracked:
      tip_spot.tracker.commit()
    self._tip = None
    self._tip_origin = None

  async def return_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Return the mounted tip to the position it came from."""
    if self._tip_origin is None:
      raise RuntimeError("The mounted tip's origin is unknown")
    await self.drop_tip(
      self._tip_origin,
      offset=offset,
      allow_nonzero_volume=allow_nonzero_volume,
    )

  async def discard_tip(
    self,
    offset: Optional[Coordinate] = None,
    allow_nonzero_volume: bool = False,
  ) -> None:
    """Discard the mounted tip into the OT-2's fixed trash."""
    self._require_single_channel()
    tip = self._require_tip()
    if does_volume_tracking() and tip.tracker.get_used_volume() > 0 and not allow_nonzero_volume:
      raise ValueError("The mounted tip still contains liquid")
    offset = offset or Coordinate.zero()
    _require_finite_coordinate("offset", offset)

    async with self.robot._operation_lock:
      if self.robot.api_version is None:
        raise RuntimeError("OT-2 API version is unavailable; call setup() first")
      if _version_at_least(
        self.robot.api_version,
        _OT_DECK_IS_ADDRESSABLE_AREA_VERSION,
      ):
        await self.robot._enqueue_command(
          "moveToAddressableAreaForDropTip",
          {
            "pipetteId": self.pipette_id,
            "addressableAreaName": "fixedTrash",
            "offset": {"x": offset.x, "y": offset.y, "z": offset.z + 10},
            "alternateDropLocation": False,
          },
        )
        await self.robot._enqueue_command(
          "dropTipInPlace",
          {"pipetteId": self.pipette_id},
        )
      else:
        await self.robot._enqueue_command(
          "dropTip",
          {
            "labwareId": "fixedTrash",
            "wellName": "A1",
            "wellLocation": {
              "origin": "bottom",
              "offset": {"x": offset.x, "y": offset.y, "z": offset.z + 10},
            },
            "pipetteId": self.pipette_id,
          },
        )

    self._tip = None
    self._tip_origin = None

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

  async def _aspirate_in_place(self, volume: float, flow_rate: float) -> None:
    await self.robot._enqueue_command(
      "aspirateInPlace",
      {"flowRate": flow_rate, "volume": volume, "pipetteId": self.pipette_id},
    )

  async def _dispense_in_place(self, volume: float, flow_rate: float) -> None:
    await self.robot._enqueue_command(
      "dispenseInPlace",
      {
        "flowRate": flow_rate,
        "volume": volume,
        "pipetteId": self.pipette_id,
        "pushOut": 0.0,
      },
    )

  async def aspirate(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Aspirate liquid from a container and return to traversal height."""
    self._require_single_channel()
    tip = self._require_tip()
    volume = self._validate_volume(volume)
    flow_rate = self._spec.default_aspiration_flow_rate if flow_rate is None else float(flow_rate)
    if not math.isfinite(flow_rate) or flow_rate <= 0:
      raise ValueError("flow_rate must be finite and greater than zero")
    offset = offset or Coordinate.zero()
    location = self._liquid_location(container, offset, liquid_height)

    tracked = does_volume_tracking()
    if tracked:
      if not container.tracker.is_disabled:
        container.tracker.remove_liquid(volume)
      tip.tracker.add_liquid(volume)

    try:
      async with self.robot._operation_lock:
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._aspirate_in_place(volume, flow_rate)
        await self._move_to(
          Coordinate(location.x, location.y, self.robot.traversal_height),
          minimum_z_height=self.robot.traversal_height,
        )
    except Exception:
      if tracked:
        if not container.tracker.is_disabled:
          container.tracker.rollback()
        tip.tracker.rollback()
      raise

    if tracked:
      if not container.tracker.is_disabled:
        container.tracker.commit()
      tip.tracker.commit()

  async def dispense(
    self,
    container: Container,
    volume: float,
    flow_rate: Optional[float] = None,
    liquid_height: float = 0,
    offset: Optional[Coordinate] = None,
  ) -> None:
    """Dispense liquid into a container and return to traversal height."""
    self._require_single_channel()
    tip = self._require_tip()
    volume = self._validate_volume(volume)
    flow_rate = self._spec.default_dispense_flow_rate if flow_rate is None else float(flow_rate)
    if not math.isfinite(flow_rate) or flow_rate <= 0:
      raise ValueError("flow_rate must be finite and greater than zero")
    offset = offset or Coordinate.zero()
    location = self._liquid_location(container, offset, liquid_height)

    tracked = does_volume_tracking()
    if tracked:
      tip.tracker.remove_liquid(volume)
      if not container.tracker.is_disabled:
        container.tracker.add_liquid(volume)

    try:
      async with self.robot._operation_lock:
        await self._move_to(
          location,
          minimum_z_height=self.robot.traversal_height,
        )
        await self._dispense_in_place(volume, flow_rate)
        await self._move_to(
          Coordinate(location.x, location.y, self.robot.traversal_height),
          minimum_z_height=self.robot.traversal_height,
        )
    except Exception:
      if tracked:
        tip.tracker.rollback()
        if not container.tracker.is_disabled:
          container.tracker.rollback()
      raise

    if tracked:
      tip.tracker.commit()
      if not container.tracker.is_disabled:
        container.tracker.commit()

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
    """Mix in place using client-side aspiration and dispense cycles."""
    self._require_single_channel()
    self._require_tip()
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
    async with self.robot._operation_lock:
      await self._move_to(location, minimum_z_height=self.robot.traversal_height)
      for _ in range(repetitions):
        await self._aspirate_in_place(volume, aspiration_flow_rate)
        await self._dispense_in_place(volume, dispense_flow_rate)
      await self._move_to(
        Coordinate(location.x, location.y, self.robot.traversal_height),
        minimum_z_height=self.robot.traversal_height,
      )


class OpentronsOT2:
  """Opentrons OT-2 liquid-handling robot controlled through its HTTP API.

  The OT-2's mounted pipettes are discovered during :meth:`setup` and exposed as
  :attr:`left_pipette` and :attr:`right_pipette` objects.
  """

  def __init__(
    self,
    host: str,
    port: int = 31950,
    deck: Optional[OTDeck] = None,
    traversal_height: float = 120,
    command_timeout: float = 30,
    command_poll_interval: float = 0.05,
    io: Optional[HTTP] = None,
  ):
    if "://" in host:
      raise ValueError("host must be a hostname or IP address without a URL scheme")
    if not 1 <= port <= 65535:
      raise ValueError("port must be between 1 and 65535")
    if not math.isfinite(traversal_height) or traversal_height < 0:
      raise ValueError("traversal_height must be finite and non-negative")
    if not math.isfinite(command_timeout) or command_timeout <= 0:
      raise ValueError("command_timeout must be finite and greater than zero")
    if not math.isfinite(command_poll_interval) or command_poll_interval < 0:
      raise ValueError("command_poll_interval must be finite and non-negative")

    self.host = host
    self.port = port
    self.deck = deck or OTDeck()
    self.geometry = OT2RobotGeometry()
    self.traversal_height = traversal_height
    self.command_timeout = command_timeout
    self.command_poll_interval = command_poll_interval
    self.io = io or HTTP(
      human_readable_device_name="Opentrons OT-2",
      base_url=f"http://{host}:{port}",
      headers={"Opentrons-Version": "3"},
      timeout=command_timeout,
    )

    self.api_version: Optional[str] = None
    self.left_pipette: Optional[OT2Pipette] = None
    self.right_pipette: Optional[OT2Pipette] = None
    self._run_id: Optional[str] = None
    self._tip_racks: Dict[str, int] = {}
    self._plr_name_to_ot_name: Dict[str, str] = {}
    self._operation_lock = asyncio.Lock()

  @property
  def pipettes(self) -> List[OT2Pipette]:
    """Mounted pipettes, left first."""
    return [p for p in (self.left_pipette, self.right_pipette) if p is not None]

  async def setup(self, skip_home: bool = False) -> None:
    """Connect, create an OT run, discover pipettes, and optionally home."""
    logger.warning(
      "OpentronsOT2 has NOT been tested against hardware in the new PyLabRobot architecture. "
      "Please make a PR to remove this message if you have verified it on your hardware."
    )
    if self._run_id is not None:
      raise RuntimeError("The OT-2 is already set up")

    await self.io.setup()
    try:
      run = await self.io.request("POST", "/runs")
      self._run_id = cast(str, run["data"]["id"])
      mounted = await self.io.request("GET", "/pipettes")
      self.left_pipette = await self._load_mounted_pipette("left", mounted)
      self.right_pipette = await self._load_mounted_pipette("right", mounted)
      health = await self.io.request("GET", "/health")
      self.api_version = cast(str, health["api_version"])
      if not skip_home:
        await self.home()
    except Exception:
      await self._cancel_run()
      self._clear_run_state()
      await self.io.stop()
      raise

  async def stop(self) -> None:
    """Cancel the active OT run and close the HTTP transport."""
    try:
      await self._cancel_run()
    finally:
      self._clear_run_state()
      await self.io.stop()

  def _clear_run_state(self) -> None:
    self._run_id = None
    self.api_version = None
    self.left_pipette = None
    self.right_pipette = None
    self._tip_racks = {}
    self._plr_name_to_ot_name = {}

  async def _cancel_run(self) -> None:
    if self._run_id is None:
      return
    requests = (
      (
        "POST",
        f"/runs/{self._run_id}/actions",
        {"data": {"actionType": "stop"}},
      ),
      ("POST", f"/runs/{self._run_id}/cancel", None),
      ("POST", f"/runs/{self._run_id}/actions/cancel", None),
      ("DELETE", f"/runs/{self._run_id}", None),
    )
    for method, path, data in requests:
      try:
        await self.io.request(method, path, data)
        return
      except Exception as error:  # noqa: BLE001 - firmware versions expose different routes
        logger.debug("OT-2 run cancellation through %s failed: %s", path, error)
    logger.warning("Could not cancel OT-2 run %s", self._run_id)

  async def _load_mounted_pipette(
    self,
    mount: Mount,
    mounted: Dict[str, Any],
  ) -> Optional[OT2Pipette]:
    pipette_name = mounted[mount]["name"]
    if pipette_name is None:
      return None
    if pipette_name not in _PIPETTE_SPECS:
      raise ValueError(f"Unsupported OT-2 pipette {pipette_name!r} on the {mount} mount")
    result = await self._enqueue_command(
      "loadPipette",
      {"pipetteName": pipette_name, "mount": mount},
    )
    return OT2Pipette(
      robot=self,
      mount=mount,
      name=cast(str, pipette_name),
      pipette_id=cast(str, result["pipetteId"]),
    )

  async def _enqueue_command(
    self,
    command_type: str,
    params: Dict[str, Any],
    intent: Literal["setup", "protocol"] = "setup",
  ) -> Dict[str, Any]:
    if self._run_id is None:
      raise RuntimeError("The OT-2 is not set up")
    response = await self.io.request(
      "POST",
      f"/runs/{self._run_id}/commands",
      {
        "data": {
          "commandType": command_type,
          "params": params,
          "intent": intent,
        }
      },
    )
    command_id = cast(str, response["data"]["id"])
    deadline = time.monotonic() + self.command_timeout
    while True:
      response = await self.io.request(
        "GET",
        f"/runs/{self._run_id}/commands/{command_id}",
      )
      data = cast(Dict[str, Any], response["data"])
      status = data["status"]
      if status == "succeeded":
        return cast(Dict[str, Any], data.get("result", {}))
      if status == "failed":
        error = cast(Dict[str, Any], data.get("error", {}))
        error_type = error.get("errorType", "unknown")
        detail = error.get("detail", "no detail returned")
        raise OpentronsOT2Error(f"{command_type} failed with {error_type}: {detail}")
      if status not in {"queued", "running"}:
        raise OpentronsOT2Error(f"{command_type} returned unexpected command status {status!r}")
      if time.monotonic() >= deadline:
        raise TimeoutError(f"Timed out waiting for OT-2 command {command_type!r}")
      await asyncio.sleep(self.command_poll_interval)

  async def home(self) -> None:
    """Home the OT-2 gantry and pipette axes."""
    if self._run_id is None:
      raise RuntimeError("The OT-2 is not set up")
    async with self._operation_lock:
      await self.io.request("POST", "/robot/home", {"target": "robot"})

  async def list_connected_modules(self) -> List[Dict[str, Any]]:
    """Return modules connected to the OT-2."""
    if self._run_id is None:
      raise RuntimeError("The OT-2 is not set up")
    response = await self.io.request("GET", "/modules")
    return cast(List[Dict[str, Any]], response["data"])

  def _ot_name(self, plr_resource_name: str) -> str:
    if plr_resource_name not in self._plr_name_to_ot_name:
      self._plr_name_to_ot_name[plr_resource_name] = uuid.uuid4().hex
    return self._plr_name_to_ot_name[plr_resource_name]

  @staticmethod
  def _well_name(tip_spot: TipSpot) -> str:
    """Return the rack-local Opentrons well identifier for a tip spot."""
    if not isinstance(tip_spot.parent, TipRack):
      raise ValueError("tip_spot must be assigned to a tip rack")
    return tip_spot.parent.get_child_identifier(tip_spot)

  async def _assign_tip_rack(self, tip_rack: TipRack, tip: Tip) -> None:
    if tip_rack.name in self._tip_racks:
      return
    slot = self.deck.get_slot(tip_rack)
    if slot is None:
      raise ValueError("tip rack must be assigned directly to an OT-2 deck slot")

    official_load_name = _OFFICIAL_TIP_RACKS.get(tip_rack.model or "")
    if official_load_name is not None:
      namespace, load_name, version = "opentrons", official_load_name, 1
    else:
      tip_spots = tip_rack.get_all_items()
      well_names = {
        tip_spot.name: tip_rack.get_child_identifier(tip_spot) for tip_spot in tip_spots
      }
      definition = {
        "schemaVersion": 2,
        "version": 1,
        "namespace": "pylabrobot",
        "metadata": {
          "displayName": self._ot_name(tip_rack.name),
          "displayCategory": "tipRack",
          "displayVolumeUnits": "µL",
        },
        "brand": {"brand": "unknown"},
        "parameters": {
          "format": (
            "96Standard"
            if (tip_rack.num_items_x, tip_rack.num_items_y) == (12, 8)
            else "384Standard"
            if (tip_rack.num_items_x, tip_rack.num_items_y) == (24, 16)
            else "irregular"
          ),
          "isTiprack": True,
          "tipLength": tip.total_tip_length,
          "tipOverlap": tip.fitting_depth,
          "loadName": self._ot_name(tip_rack.name),
          "isMagneticModuleCompatible": False,
        },
        "ordering": utils.reshape_2d(
          [well_names[tip_spot.name] for tip_spot in tip_spots],
          (tip_rack.num_items_x, tip_rack.num_items_y),
        ),
        "cornerOffsetFromSlot": {
          "x": 0,
          "y": 0,
          "z": 0,
        },
        "dimensions": {
          "xDimension": tip_rack.get_absolute_size_x(),
          "yDimension": tip_rack.get_absolute_size_y(),
          "zDimension": tip_rack.get_absolute_size_z(),
        },
        "wells": {
          well_names[child.name]: {
            "depth": tip.total_tip_length,
            "x": cast(Coordinate, child.location).x + child.get_absolute_size_x() / 2,
            "y": cast(Coordinate, child.location).y + child.get_absolute_size_y() / 2,
            "z": cast(Coordinate, child.location).z,
            "shape": "circular",
            "diameter": math.hypot(
              child.get_absolute_size_x(),
              child.get_absolute_size_y(),
            ),
            "totalLiquidVolume": tip.maximal_volume,
          }
          for child in tip_rack.children
        },
        "groups": [
          {
            "wells": [well_names[tip_spot.name] for tip_spot in tip_spots],
            "metadata": {},
          }
        ],
      }
      response = await self.io.request(
        "POST",
        f"/runs/{self._run_id}/labware_definitions",
        {"data": definition},
      )
      namespace, load_name, version_text = cast(
        str,
        response["data"]["definitionUri"],
      ).split("/")
      version = int(version_text)
    await self._enqueue_command(
      "loadLabware",
      {
        "location": {"slotName": str(slot)},
        "loadName": load_name,
        "namespace": namespace,
        "version": version,
        "labwareId": self._ot_name(tip_rack.name),
        "displayName": self._ot_name(tip_rack.name),
      },
    )
    self._tip_racks[tip_rack.name] = slot

  def _deck_to_robot_frame(self, location: Coordinate) -> Coordinate:
    return location - self.deck.slot_locations[0]
