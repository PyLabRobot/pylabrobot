import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type, cast

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.api import HTTP_API_VERSION, OpentronsAPI
from pylabrobot.opentrons.flex.checks import traversal_z
from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex_gripper import FlexGripper
from pylabrobot.opentrons.flex.flex_head import FlexHead1, FlexHead8, FlexHead96, _FlexHead
from pylabrobot.opentrons.flex.flex_wire import (
  ROBOT_AXES,
  _require_robot_commands,
)
from pylabrobot.opentrons.labware import (
  LabwareRegistry,
  build_container_definition,
  build_movable_labware_definition,
  build_plate_definition,
  build_tip_rack_definition,
  official_tip_rack_identity,
)
from pylabrobot.opentrons.operations import OperationLock, serialized
from pylabrobot.opentrons.run import COMMAND_POLL_HEADROOM, OpentronsRun
from pylabrobot.opentrons.types import (
  InstrumentInfo,
  LabwareIdentity,
  ModuleInfo,
  Mount,
  PipetteInfo,
  RobotInfo,
  RunInfo,
)
from pylabrobot.resources import Container, Plate, Resource, TipRack
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)

# Discovered pipette channel count -> matching head class.
_CHANNELS_TO_HEAD: Dict[int, Type[_FlexHead]] = {
  1: FlexHead1,
  8: FlexHead8,
  96: FlexHead96,
}


def _has_pipettable_geometry(resource: Resource) -> bool:
  """Whether a real, well-bearing definition can be built from this resource."""
  return isinstance(resource, (Plate, TipRack, Container))


def _not_pipettable_error(resource: Resource) -> OpentronsError:
  """The refusal for a resource no pipettable definition can be built from."""
  return OpentronsError(
    "Cannot build an Opentrons labware definition",
    f"'{resource.name}' ({type(resource).__name__}) has no Opentrons load name, and a "
    "definition can only be built from the geometry of a Plate, TipRack, or Container. "
    "The gripper can still "
    "move it -- only pipetting needs real well geometry.",
  )


def _warn_grip_distance_discarded(
  name: str, grip_distance_from_top: Optional[float], why: str
) -> None:
  """Log a caller's grip distance being dropped, naming why it cannot apply."""
  if grip_distance_from_top is None:
    return
  logger.warning(
    "grip_distance_from_top=%s ignored for '%s': %s.", grip_distance_from_top, name, why
  )


def _validate_axes(axes: Iterable[str]) -> None:
  """Raise for any axis name the robot does not address."""
  unknown = sorted(set(axes) - ROBOT_AXES)
  if unknown:
    raise ValueError(
      f"unknown axes: {', '.join(unknown)}. Valid axes are: {', '.join(sorted(ROBOT_AXES))}."
    )


def _reported_axis_position(command: Dict[str, Any]) -> Dict[str, float]:
  """The axis positions a completed robot/moveAxes* command reports."""
  return cast(Dict[str, float], command.get("result", {}).get("position", {}))


def _axis_motion_params(
  axis_map: Dict[str, float],
  speed: Optional[float],
  critical_point: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
  """The snake_case payload a robot/moveAxes* command takes (unlike the rest of
  the API, which is camelCase), with every axis name validated first."""
  _validate_axes(axis_map)
  params: Dict[str, Any] = {"axis_map": axis_map}
  if critical_point is not None:
    _validate_axes(critical_point)
    params["critical_point"] = critical_point
  if speed is not None:
    params["speed"] = speed
  return params


class Flex:
  """Opentrons Flex liquid handler, over the robot-server HTTP API.

  A device shell composed on the shared ``pylabrobot.io.HTTP`` transport
  (``OpentronsAPI`` + a run-scoped ``OpentronsRun``): it owns the deck, deck-scoped
  labware loading, and the discover-then-compose lifecycle that builds
  mount-addressed head sub-objects (``left``/``right``/``head96``).
  Liquid-handling ops live on the heads, not here — see
  :mod:`pylabrobot.opentrons.flex.flex_head`.

  ``connect()`` opens the transport for health/discovery queries; ``setup()``
  also creates a run, homes, and composes the heads.
  """

  def __init__(
    self,
    host: str,
    port: int = 31950,
    deck: Optional[FlexDeck] = None,
    io: Optional[HTTP] = None,
    command_timeout: float = 30.0,
    command_poll_interval: float = 0.05,
  ) -> None:
    if "://" in host:
      raise ValueError("host must be a hostname or IP address without a URL scheme")
    if not 1 <= port <= 65535:
      raise ValueError("port must be between 1 and 65535")
    if not math.isfinite(command_timeout) or command_timeout <= 0:
      raise ValueError("command_timeout must be finite and greater than zero")
    if not math.isfinite(command_poll_interval) or command_poll_interval < 0:
      raise ValueError("command_poll_interval must be finite and non-negative")

    self.host, self.port = host, port
    self.base_url = f"http://{host}:{port}"
    self.command_timeout = command_timeout
    self.command_poll_interval = command_poll_interval
    # Built here rather than on connect: a pylabrobot io refuses construction
    # once a capture is armed, so a robot built first can still be recorded.
    self.io: HTTP = io or HTTP(
      human_readable_device_name="Opentrons Flex",
      base_url=self.base_url,
      headers={"Opentrons-Version": HTTP_API_VERSION},
      timeout=command_timeout,
    )
    self._api = OpentronsAPI(self.io)
    self._operation_lock = OperationLock()
    self._connected = False
    self._run: Optional[OpentronsRun] = None
    self.run_id: Optional[str] = None
    self.api_version: Optional[str] = None
    self.robot_model: Optional[str] = None

    self.deck = deck or FlexDeck()
    self._labware: Optional[LabwareRegistry] = None
    # Names loaded under the non-pipettable movable stub. Tracked separately so
    # a pipetting op refuses them even on a load-cache hit.
    self._stub_labware: Set[int] = set()
    self.left: Optional[_FlexHead] = None
    self.right: Optional[_FlexHead] = None
    self.head96: Optional[_FlexHead] = None
    self.gripper: Optional[FlexGripper] = None
    self._heads: List[_FlexHead] = []

  @property
  def traversal_height(self) -> float:
    """Minimum tip/nozzle Z for travel, above the tallest modeled labware.

    Includes the deck's tip-rack clearance floor. Describe all physical
    obstacles in the deck model so taller labware raises this height.
    """
    return traversal_z(self.deck)

  @property
  def software_version(self) -> Optional[str]:
    """Robot software version read at connection time."""
    return self.api_version

  @property
  def left_pipette(self) -> Optional[_FlexHead]:
    """Pipette on the left mount, also available as ``left``."""
    return self.left

  @property
  def right_pipette(self) -> Optional[_FlexHead]:
    """Pipette on the right mount, also available as ``right``."""
    return self.right

  @property
  def pipettes(self) -> List[_FlexHead]:
    """Mounted pipettes in discovery order, including a 96-channel head."""
    return list(self._heads)

  def attach_deck(self, deck: FlexDeck) -> None:
    """Replace the deck before setup or after stopping the active run."""
    if self._run is not None:
      raise RuntimeError("Stop the active run before replacing its deck")
    self.deck = deck
    self._stub_labware.clear()

  # --- Lifecycle ---

  @serialized
  async def setup(self, skip_home: bool = False) -> None:
    """Bring the robot fully up, PyLabRobot's usual one-call lifecycle entry.

    Composed of the steps below so a caller who needs only some of them (talk to
    the robot without moving it, hand it back without homing it) can take them
    one at a time.
    """
    if self._run is not None:
      raise RuntimeError("The Flex is already set up")
    try:
      await self.connect()
      await self.create_run()
      if not skip_home:
        await self.home()
      await self.initialize()
    except BaseException:
      await self.disconnect()
      raise

  @serialized
  async def connect(self) -> None:
    """Open the link and confirm the robot answers. Starts no run, moves nothing."""
    if self._connected:
      return
    await self.io.setup()
    try:
      health = await self._api.get_health()
    except BaseException:
      await self.io.stop()
      raise
    self.api_version = health.software_version
    self.robot_model = health.model
    self._connected = True
    logger.info(
      "Connected to robot '%s' at %s:%s (API %s, model: %s)",
      health.name,
      self.host,
      self.port,
      self.api_version,
      self.robot_model,
    )

  @serialized
  async def create_run(self) -> None:
    """Start a control session.

    The robot reports itself as in use and refuses its own touchscreen for as
    long as a run is current, so this is the step that takes it from the
    operator, not ``connect``. Cancels a run this object already holds first;
    ``run_id`` is the only handle on a run, so overwriting it would strand the
    old one on the robot with nothing left able to release it.

    labwareIds and uploaded definitions are both run-scoped server-side, so a
    new run must not serve cached identities from a previous one.
    """
    if not self._connected:
      raise RuntimeError("The Flex is not connected")
    await self.cancel_run()
    receipt = await self._api.create_run()
    self.run_id = receipt.id
    self._run = OpentronsRun(
      self._api,
      receipt.id,
      self.api_version or "",
      command_timeout=self.command_timeout,
      command_poll_interval=self.command_poll_interval,
    )
    if self._run is not None:
      self._labware = LabwareRegistry(self._run, server_assigned_ids=True)
    self._stub_labware.clear()
    logger.info("Created run %s", self.run_id)

  @serialized
  async def initialize(self) -> None:
    """Discover what is mounted and compose the heads. Moves nothing."""
    # Discover ALL mounted pipettes and compose the matching head per mount.
    # Discovery is re-runnable: drop whatever a previous setup composed rather
    # than stacking a second set of heads onto dead pipette ids.
    self.left = self.right = self.head96 = None
    self.gripper = None
    self._heads.clear()

    instruments_data = await self.get_instruments()
    pipettes = self._parse_pipettes(instruments_data)

    if not pipettes:
      raise OpentronsError("No pipette detected", f"{self.host}:{self.port}")

    if any(pip.channels == 96 for pip in pipettes) and len(pipettes) > 1:
      raise OpentronsError(
        "Impossible instrument combination",
        "A 96-channel head cannot be mounted alongside another pipette on a Flex.",
      )

    for pip in pipettes:
      pipette_id = await self._require_run().load_pipette(pip.pipette_name, cast(Mount, pip.mount))
      head_cls = _CHANNELS_TO_HEAD.get(pip.channels)
      if head_cls is None:
        raise OpentronsError(
          "Unsupported pipette channel count",
          f"{pip.channels} channels (mount '{pip.mount}') has no matching FlexHead.",
        )
      head = head_cls(self, pip.mount, pipette_id, pip.channels, pip.pipette_model, pip.max_volume)

      if pip.channels == 96:
        self.head96 = head
      elif pip.mount == "left":
        self.left = head
      elif pip.mount == "right":
        self.right = head
      else:
        raise OpentronsError("Unknown mount", f"mount '{pip.mount}' is neither 'left' nor 'right'.")
      self._heads.append(head)

    # The gripper (extension mount) is optional: compose it when discovery
    # reports one, leave ``self.gripper`` None otherwise.
    gripper_model = next(
      (i.model for i in instruments_data if i.instrument_type == "gripper"), None
    )
    if gripper_model is not None:
      self.gripper = FlexGripper(self, gripper_model)
      logger.info("Discovered gripper on the extension mount (model: %s)", gripper_model)

  @serialized
  async def cancel_run(self) -> None:
    """End the control session, handing the robot back to its own touchscreen.

    Safe with no run open. This, not ``disconnect``, is what frees local
    control: the run is what the robot holds, and it outlives our link.
    """
    if self._run is not None:
      await self._run.stop()
    self._run = None
    self._labware = None
    self.run_id = None
    self.left = self.right = self.head96 = None
    self.gripper = None
    self._heads.clear()

  @serialized
  async def disconnect(self) -> None:
    """Drop the link, moving nothing. Cancels an open run first."""
    await self.cancel_run()
    if self._connected:
      await self.io.stop()
      self._connected = False

  def _require_run(self) -> OpentronsRun:
    """Return the active run or reject commands outside a control session."""
    if self._run is None or not self._run.active:
      raise RuntimeError("The Flex is not set up")
    return self._run

  @serialized
  async def home(self) -> Dict[str, Any]:
    """Home all axes. The gantry moves to the rear-left-top."""
    return await self._execute_command("home", {})

  @serialized
  async def stop(self) -> None:
    """Park the gantry and release the robot, dropping any mounted tips first."""
    # Drop any mounted tips to the trash BEFORE parking/disconnecting, so the
    # robot is never left holding tips. A failure here must not block the
    # home/cancel/disconnect that follows.
    try:
      trash: Optional[Trash] = self.deck.get_trash_area()
    except ValueError:
      trash = None
    if trash is not None:
      for head in reversed(self._heads):
        try:
          if any(tip is not None for tip in head.get_mounted_tips()):
            await head.discard_tips(trash)
        except Exception:
          logger.warning(
            "Dropping tips on stop failed for the %s head; continuing to disconnect.",
            head.mount,
            exc_info=True,
          )
    # Home inside the run (before cancelling it) so the gantry parks in a known
    # pose; a failure here must not block the release that follows.
    try:
      await self.home()
    except Exception:
      logger.warning("home() before stop failed; continuing to release", exc_info=True)
    await self.cancel_run()
    await self.disconnect()

  # --- Command execution + discovery (over api/run) ---

  async def _execute_command(
    self,
    command_type: str,
    params: Optional[Dict[str, Any]] = None,
    wait: bool = True,
    timeout: Optional[float] = None,
  ) -> Dict[str, Any]:
    """Run a command in the current run, returning the completed command dict.

    The single seam the heads and gripper submit every robot-server command
    through. Returns the full command dict (with its ``result``), raising
    :class:`OpentronsCommandError` on failure and ``OpentronsCommandTimeout``
    if it does not complete in time.
    """
    if self._run is None:
      raise OpentronsError("No active run", "Call setup() or create_run() first.")
    command = await self._run.execute(command_type, params or {}, wait=wait, timeout=timeout)
    return {
      "id": command.id,
      "status": command.status,
      "result": command.result,
      "error": command.error,
    }

  @serialized
  async def send_command(
    self,
    command_type: str,
    params: Optional[Dict[str, Any]] = None,
    wait: bool = True,
    timeout: Optional[float] = None,
  ) -> Dict[str, Any]:
    """Send any robot command by name, for the parts of the robot this class does not wrap.

    ``command_type`` is the robot's own command name (``"moveToWell"``,
    ``"heaterShaker/setTargetTemperature"``) and ``params`` its payload; both
    pass through untouched and the completed command dict is returned. Validates
    nothing and updates no resource-tree state, so a command that moves labware
    or changes tip/liquid state leaves PyLabRobot's own trackers unchanged.
    """
    return await self._execute_command(command_type, params or {}, wait=wait, timeout=timeout)

  @serialized
  async def get_health(self) -> RobotInfo:
    """Read fresh robot health without creating a run."""
    if not self._connected:
      raise RuntimeError("The Flex is not connected")
    return await self._api.get_health()

  @serialized
  async def get_instruments(self) -> Tuple[InstrumentInfo, ...]:
    """Read physical instruments before setup, without loading them into a run."""
    if not self._connected:
      raise RuntimeError("The Flex is not connected")
    return await self._api.get_instruments()

  @serialized
  async def get_runs(self) -> Tuple[RunInfo, ...]:
    """Read server runs without selecting or stopping them."""
    if not self._connected:
      raise RuntimeError("The Flex is not connected")
    return await self._api.get_runs()

  @serialized
  async def list_connected_modules(self) -> Tuple[ModuleInfo, ...]:
    """Read module identities without loading them into a run."""
    if not self._connected:
      raise RuntimeError("The Flex is not connected")
    return await self._api.get_connected_modules()

  def _parse_pipettes(self, instruments: Tuple[InstrumentInfo, ...]) -> List[PipetteInfo]:
    """Select the validated pipette specifications from discovery."""
    pipettes = []
    for instrument in instruments:
      if instrument.instrument_type != "pipette":
        continue
      assert instrument.channels is not None
      assert instrument.minimum_volume is not None and instrument.maximum_volume is not None
      pipettes.append(
        PipetteInfo(
          instrument.mount,
          instrument.name,
          instrument.model,
          "",
          instrument.channels,
          instrument.minimum_volume,
          instrument.maximum_volume,
        )
      )
    return pipettes

  # --- Deck-scoped labware loading ---

  async def _ensure_labware_loaded(
    self,
    resource: Resource,
    allow_stub: bool = False,
    grip_distance_from_top: Optional[float] = None,
  ) -> str:
    """Resolve and bind a resource once per run, validating its current slot."""
    registry = self._require_labware()
    name = resource.name
    if id(resource) in self._stub_labware and not allow_stub:
      raise _not_pipettable_error(resource)
    slot = self.deck.get_slot(resource)
    if slot is None:
      raise OpentronsError("Resource not on deck", f"'{name}' is not on a deck slot.")
    identity = official_tip_rack_identity(resource) if isinstance(resource, TipRack) else None
    if registry.is_loaded(resource):
      binding = registry.get(resource)
      await registry.load(resource, slot, identity or binding.identity)
      _warn_grip_distance_discarded(
        name, grip_distance_from_top, "it is already loaded in this run"
      )
      return binding.labware_id
    if identity is None:
      identity = await self._define_custom_labware(resource, grip_distance_from_top, allow_stub)
    else:
      _warn_grip_distance_discarded(
        name,
        grip_distance_from_top,
        f"it loads the robot's own definition for '{identity.load_name}', whose grip height is defined there",
      )
    await registry.load(resource, slot, identity)
    binding = registry.get(resource)
    logger.info("Loaded labware '%s' at slot %s -> ID: %s", name, slot, binding.labware_id)
    return binding.labware_id

  def _require_labware(self) -> LabwareRegistry:
    """Return the active run's resource bindings."""
    self._require_run()
    if self._labware is None:
      raise RuntimeError("The Flex labware registry is unavailable")
    return self._labware

  @serialized
  async def sync_tips_to_robot(self, tip_rack: TipRack) -> None:
    """Make the robot's tip-rack model agree with PyLabRobot's.

    The robot keeps its own record of which spots in a tip rack still hold a
    tip, and it is only ever changed by pickups and drops it performed itself.
    So anything that changes the rack outside a run -- a human taking tips, a
    rack swapped for a partly-used one, state restored from a database --
    leaves the two records disagreeing, with no symptom until the robot picks
    up from a spot it believes is full.

    This pushes PyLabRobot's record onto the robot in one ``setTipState`` per
    state, taking the resource tree as the truth. Set the tip layout first
    (``tip_rack.set_tip_state(...)``), then call this.

    There is no equivalent for liquid. ``loadLiquid`` is the robot's only way
    in, and it refuses any liquid the run did not already define, which a run
    created without a protocol never does. Well volumes therefore live only in
    the resource tree.
    """
    labware_id = await self._ensure_labware_loaded(tip_rack)
    # The robot grades a tip-rack well "clean"/"used"/"empty". PyLabRobot only
    # records whether a tip is there, so an occupied spot maps to "clean".
    occupied: List[str] = []
    empty: List[str] = []
    for spot in tip_rack.get_all_items():
      (occupied if spot.has_tip() else empty).append(tip_rack.get_child_identifier(spot))

    for well_names, state in ((occupied, "clean"), (empty, "empty")):
      if not well_names:
        continue
      await self._execute_command(
        "setTipState",
        {"labwareId": labware_id, "wellNames": well_names, "tipWellState": state},
      )
    logger.info(
      "Synced '%s' tip state to the robot: %d with a tip, %d empty",
      tip_rack.name,
      len(occupied),
      len(empty),
    )

  @serialized
  async def labware_moved_off_deck(self, resource: Resource) -> None:
    """Tell the robot an EXTERNAL agent (human or lab transporter) removed labware.

    A logical move, not a gripper motion: ``moveLabware`` with strategy
    ``manualMoveWithoutPause`` drops the labware from the robot-server's deck
    model, freeing its slot. Without this the slot stays occupied server-side
    and a later load into it fails with ``LocationIsOccupiedError``. The
    PLR-side deck slot is freed too. No wire command is sent for labware that
    was never loaded into the run; a re-add later loads fresh at its new slot
    and re-uploads its definition -- a different same-named resource must not
    inherit the departed labware's geometry.
    """
    name = resource.name
    registry = self._require_labware()
    if registry.is_loaded(resource):
      await self._execute_command(
        "moveLabware",
        {
          "labwareId": registry.get(resource).labware_id,
          "newLocation": "offDeck",
          "strategy": "manualMoveWithoutPause",
        },
      )
    registry.remove(resource)
    self._stub_labware.discard(id(resource))
    slot = self.deck.get_slot(resource)
    if slot is not None:
      self.deck.unassign_child_at_slot(slot)
    logger.info("Labware '%s' marked moved off-deck", name)

  @serialized
  async def reload_labware(self, resource: Resource) -> None:
    """Re-read the labware's position after a human moved it in its own slot.

    For labware nudged or re-seated where it already sits: the robot re-applies
    its labware offset, keeping the slot. Use ``gripper.move_labware`` to move
    it to another slot, and ``labware_moved_off_deck`` when it leaves the deck.
    """
    labware_id = await self._ensure_labware_loaded(resource)
    await self._execute_command("reloadLabware", {"labwareId": labware_id})

  # --- Robot-level commands: axis motion, status surfaces, run log ---

  @serialized
  async def move_axes_to(
    self,
    axis_map: Dict[str, float],
    critical_point: Optional[Dict[str, float]] = None,
    speed: Optional[float] = None,
  ) -> Dict[str, float]:
    """Move the named axes to absolute deck-frame positions (mm), returning where they land.

    Every axis the map does not name holds its current position.
    ``critical_point`` offsets the point on the mount that is driven to those
    positions, also as a per-axis map. ``speed`` is in mm/s (robot default if
    None).

    Raises:
      ValueError: If any axis is not one the robot addresses. Raised before
        any wire command is sent.
    """
    _require_robot_commands("robot/moveAxesTo", self.api_version)
    params = _axis_motion_params(axis_map, speed, critical_point)
    return _reported_axis_position(await self._execute_command("robot/moveAxesTo", params))

  @serialized
  async def move_axes_relative(
    self, axis_map: Dict[str, float], speed: Optional[float] = None
  ) -> Dict[str, float]:
    """Jog the named axes by distances (mm) from where they are, returning where they land.

    Raises:
      ValueError: If any axis is not one the robot addresses. Raised before
        any wire command is sent.
    """
    _require_robot_commands("robot/moveAxesRelative", self.api_version)
    params = _axis_motion_params(axis_map, speed)
    return _reported_axis_position(await self._execute_command("robot/moveAxesRelative", params))

  @serialized
  async def retract_axis(self, axis: str) -> None:
    """Retract ``axis`` to its home position, clearing the deck below it.

    Raises:
      ValueError: If ``axis`` is not one the robot addresses. Raised before
        any wire command is sent.
    """
    _validate_axes([axis])
    await self._execute_command("retractAxis", {"axis": axis})

  @serialized
  async def set_status_bar(self, animation: str) -> None:
    """Play a built-in light-bar animation: "idle", "confirm", "updating", "disco" or "off".

    An animation is all the status bar takes: the robot owns the colours and
    the timing, so there is nothing per-light to address.
    """
    await self._execute_command("setStatusBar", {"animation": animation})

  @serialized
  async def set_rail_lights(self, on: bool) -> None:
    """Turn the deck rail lights on or off."""
    await self._execute_command("setRailLights", {"on": on})

  @serialized
  async def add_comment(self, message: str) -> None:
    """Record ``message`` in the run's command log; the robot does nothing else with it."""
    await self._execute_command("comment", {"message": message})

  @serialized
  async def wait_for_duration(self, seconds: float) -> None:
    """Hold the run for ``seconds`` before the next command runs."""
    # The command only completes once the robot finishes waiting, so the poll
    # gets the wait itself plus the usual command headroom.
    await self._execute_command(
      "waitForDuration", {"seconds": seconds}, timeout=seconds + COMMAND_POLL_HEADROOM
    )

  async def _define_custom_labware(
    self,
    resource: Resource,
    grip_distance_from_top: Optional[float] = None,
    allow_stub: bool = False,
  ) -> LabwareIdentity:
    """Upload a geometry-derived definition for labware with no official Opentrons definition.

    Return the server's definition identity, cached until the labware leaves
    the deck or a new run starts. ``grip_distance_from_top`` only affects the
    first upload.
    """
    registry = self._require_labware()
    identity = registry.definition(resource)
    if identity is not None:
      _warn_grip_distance_discarded(
        resource.name, grip_distance_from_top, "its custom definition was already uploaded"
      )
      return identity
    definition = self._build_labware_definition(resource, grip_distance_from_top, allow_stub)
    identity = await registry.define(resource, definition)
    if not _has_pipettable_geometry(resource):
      self._stub_labware.add(id(resource))
    return identity

  @staticmethod
  def _build_labware_definition(
    resource: Resource,
    grip_distance_from_top: Optional[float] = None,
    allow_stub: bool = False,
  ) -> dict:
    """Build the definition matching the resource's type, or raise for unbuildable labware.

    Pipettable types get real-geometry definitions. Any other resource (lid,
    adapter, tube rack, ...) has no wells the robot can pipette, so it builds
    only as the non-pipettable movable stub, which ``allow_stub`` opts into.
    Geometry an Opentrons definition cannot describe (a rotated plate, a
    cavity floor the resource never declared) is refused here, before any
    wire command, rather than uploaded for the robot to act on.
    """
    try:
      if isinstance(resource, Plate):
        return build_plate_definition(resource, grip_distance_from_top)
      if isinstance(resource, TipRack):
        return build_tip_rack_definition(
          resource, for_flex=True, grip_distance_from_top=grip_distance_from_top
        )
      if isinstance(resource, Container):
        return build_container_definition(resource, grip_distance_from_top)
    except ValueError as e:
      raise OpentronsError("Cannot build an Opentrons labware definition", str(e)) from e
    if not allow_stub:
      raise _not_pipettable_error(resource)
    return build_movable_labware_definition(resource, grip_distance_from_top)
