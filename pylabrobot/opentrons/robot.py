import abc
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from pylabrobot.opentrons.transport import HttpxTransport, OpentronsTransport

logger = logging.getLogger(__name__)

# Seconds of polling a command gets on top of however long its own motion takes.
COMMAND_POLL_HEADROOM = 30.0

# One request/response with the robot-server. A read of robot state, not a motion.
DEFAULT_REQUEST_TIMEOUT = 30.0

# One command, including the motion it performs. Above the slowest single move this
# class wraps: a gripper labware move (pick, travel, place) runs to about two minutes.
DEFAULT_COMMAND_TIMEOUT = 120.0

# Delay between two reads of a running command's status.
DEFAULT_STATUS_POLL_INTERVAL = 0.2


def _plunger_seconds(params: Dict[str, Any]) -> float:
  """How long a command's own plunger travel takes, from its volume and rate.

  A 1000 uL aspirate at a viscous-liquid flow rate outlasts any fixed poll
  timeout, and giving up mid-command rolls the trackers back while the robot
  keeps pipetting.
  """
  volume, flow_rate = params.get("volume"), params.get("flowRate")
  if not isinstance(volume, (int, float)) or not isinstance(flow_rate, (int, float)):
    return 0.0
  return abs(volume) / flow_rate if flow_rate > 0 else 0.0


class OpentronsError(Exception):
  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title, self.message = title, message
    super().__init__(f"{title}: {message}" if message else title)


class OpentronsCommandError(RuntimeError):
  """A robot-server command completed with status "failed".

  Carries the command's error payload (the robot-server's ErrorOccurrence
  dict) so callers can react to defined errors by ``error_type`` (e.g.
  ``"liquidNotFound"``) instead of parsing the message string.
  """

  def __init__(self, command_type: str, error: Dict[str, Any]) -> None:
    super().__init__(f"Opentrons command '{command_type}' failed: {error.get('detail', error)}")
    self.command_type = command_type
    self.error = error

  @property
  def error_type(self) -> Optional[str]:
    """The machine-readable error identifier, e.g. "liquidNotFound"."""
    return cast(Optional[str], self.error.get("errorType"))


@dataclass
class PipetteInfo:
  mount: str
  pipette_name: str
  pipette_model: str
  pipette_id: str
  channels: int
  min_volume: float
  max_volume: float


class OpentronsRobot(abc.ABC):
  """Shared base for Opentrons HTTP robots (Flex, OT-2).

  Owns the wire transport, the run/command protocol, and instrument discovery.
  Subclasses implement the liquid-handling ops and any model-specific setup.

  Transport is an :class:`~pylabrobot.opentrons.transport.OpentronsTransport`
  held on the instance — a real ``HttpxTransport`` by default, or a stand-in
  (e.g. ``ChatterboxTransport``) injected by the caller for offline use.
  PyLabRobot has no pylabrobot.io HTTP transport yet, so this seam lives here
  rather than behind a pylabrobot.io primitive.
  """

  def __init__(
    self,
    host: str,
    port: int = 31950,
    transport: Optional[OpentronsTransport] = None,
    request_timeout: Optional[float] = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    status_poll_interval: float = DEFAULT_STATUS_POLL_INTERVAL,
  ) -> None:
    """Args:
    host: the robot's address.
    port: the robot-server's port.
    transport: wire transport to use instead of a real one, for offline runs.
    request_timeout: how long one request/response may take, in seconds. Only a
      transport this robot builds itself can carry it, so passing it alongside a
      ``transport`` is refused; give that transport the budget instead. The
      attribute reads ``None`` when a transport came in: the budget is that
      transport's, and this object does not know it.
    command_timeout: how long to poll a command before giving up, in seconds. A
      command that names its own motion time gets ``max(command_timeout, motion +
      COMMAND_POLL_HEADROOM)``.
    status_poll_interval: delay between two command-status reads, in seconds.
    """
    if transport is not None and request_timeout is not None:
      raise ValueError(
        "request_timeout does not reach an injected transport. Build the transport "
        "with the budget you want, e.g. HttpxTransport(base_url, timeout=...)."
      )
    for name, value in (
      ("request_timeout", request_timeout),
      ("command_timeout", command_timeout),
      ("status_poll_interval", status_poll_interval),
    ):
      if value is not None and value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {value}")
    self.host, self.port = host, port
    self.base_url = f"http://{host}:{port}"
    self.command_timeout = command_timeout
    self.status_poll_interval = status_poll_interval
    # Built here rather than on connect: a pylabrobot io refuses construction
    # once a capture is armed, so a robot built first can still be recorded.
    if transport is None:
      self.request_timeout: Optional[float] = (
        DEFAULT_REQUEST_TIMEOUT if request_timeout is None else request_timeout
      )
      self._transport: OpentronsTransport = HttpxTransport(
        base_url=self.base_url, timeout=self.request_timeout
      )
    else:
      self.request_timeout = None
      self._transport = transport
    self.run_id: Optional[str] = None
    self.pipette: Optional[PipetteInfo] = None
    self.api_version: Optional[str] = None
    self.robot_model: Optional[str] = None

  async def setup(self) -> None:
    """Bring the robot fully up, PyLabRobot's usual one-call lifecycle entry.

    Composed of the steps below so a caller who needs only some of them (talk to
    the robot without moving it, hand it back without homing it) can take them
    one at a time.
    """
    await self.connect()
    await self.create_run()
    await self.home()
    await self.initialize()

  async def stop(self) -> None:
    """Park the gantry and release the robot, PyLabRobot's usual teardown."""
    # Home inside the run (before cancelling it) so the gantry parks in a known
    # pose; a failure here must not block the release that follows.
    try:
      await self.home()
    except Exception:
      logger.warning("home() before stop failed; continuing to release", exc_info=True)
    await self.cancel_run()
    await self.disconnect()

  async def connect(self) -> None:
    """Open the link and confirm the robot answers. Starts no run, moves nothing."""
    await self._connect()

  async def create_run(self) -> None:
    """Start a control session.

    The robot reports itself as in use and refuses its own touchscreen for as
    long as a run is current, so this is the step that takes it from the
    operator, not ``connect``.

    Cancels a run this object already holds before opening the next one.
    ``run_id`` is the only handle we have on a run, so overwriting it strands
    the old one on the robot, still current, with nothing left able to release
    it: the touchscreen stays locked and a power cycle is the only way out.
    """
    await self._cancel_run()
    await self._create_run()

  async def initialize(self) -> None:
    """Discover what is mounted and get it ready to command. Moves nothing.

    Homing is ``home()``, deliberately not folded in here: a caller that wants
    to know what is on the robot should not have to move it to find out.
    """
    await self._model_setup()

  async def cancel_run(self) -> None:
    """End the control session, handing the robot back to its own touchscreen.

    Safe with no run open. This, not ``disconnect``, is what frees local
    control: the run is what the robot holds, and it outlives our link.
    """
    await self._cancel_run()

  async def disconnect(self) -> None:
    """Drop the link, moving nothing.

    Cancels an open run first, since a run left current keeps the robot locked
    out of its own controls with nothing left to release it.
    """
    await self._cancel_run()
    await self._disconnect()

  @abc.abstractmethod
  async def _model_setup(self) -> None:
    """Model-specific post-connection setup (home, discover + load pipette(s), etc.).

    Pipette discovery is entirely the subclass's job: the base ``setup()``
    does not call ``_discover_pipette()`` itself, so a model that loads a
    single pipette (e.g. a future OT-2 subclass) should call
    ``self.pipette = await self._discover_pipette()`` here; a model that
    composes multiple mount-addressed heads (e.g. ``OpentronsFlex``) should
    discover and load each pipette itself instead. This avoids loading the
    same pipette twice.
    """

  # --- Connection Lifecycle ---

  async def _connect(self) -> None:
    """Open the transport and verify connectivity.

    Sends a health check to confirm the robot is reachable and the robot
    server is running (not in Jupyter/Python API mode).
    """
    await self._transport.setup()
    health = await self._get("/health")
    self.api_version = health.get("api_version")
    self.robot_model = health.get("robot_model", "")
    robot_name = health.get("name", "unknown")
    logger.info(
      "Connected to robot '%s' at %s:%s (API %s, model: %s)",
      robot_name,
      self.host,
      self.port,
      self.api_version,
      self.robot_model,
    )

  async def _disconnect(self) -> None:
    """Close the transport, keeping the object so a reconnect can reopen it.

    Discarding it here would mean rebuilding an io on the next connect, which
    a capture armed in the meantime refuses.
    """
    await self._transport.close()

  async def send_command(
    self,
    command_type: str,
    params: Optional[Dict[str, Any]] = None,
    wait: bool = True,
    timeout: Optional[float] = None,
  ) -> Dict[str, Any]:
    """Send any robot command by name, for the parts of the robot this class does not wrap.

    The robot accepts far more commands than this driver exposes as methods:
    the module families (heater-shaker, thermocycler, temperature, magnetic,
    absorbance reader, vacuum, Flex Stacker), calibration, and whatever a
    later robot software release adds. ``command_type`` is the robot's own
    command name (``"moveToWell"``, ``"heaterShaker/setTargetTemperature"``)
    and ``params`` its payload; both are passed through untouched, and the
    returned dict is the completed command including its ``result``.

    Prefer a typed method where one exists. This one validates nothing and
    updates no resource-tree state, so a command that moves labware or changes
    tip or liquid state leaves PyLabRobot's own trackers describing the state
    before it ran.
    """
    return await self._execute_command(command_type, params or {}, wait=wait, timeout=timeout)

  # --- Low-Level Wire Calls ---

  async def _get(self, path: str) -> Dict[str, Any]:
    """Wire GET, return parsed JSON."""
    return await self._transport.get(path)

  async def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Wire POST, return parsed JSON."""
    return await self._transport.post(path, json=data or {})

  async def _delete(self, path: str) -> Dict[str, Any]:
    """Wire DELETE, return parsed JSON."""
    return await self._transport.delete(path)

  # --- Run Management ---

  async def _create_run(self) -> str:
    """Create a new empty run on the robot. Returns the run ID.

    An empty run (no protocolId) allows sending setup commands
    interactively, which is how PLR controls the robot.
    """
    result = await self._post("/runs", {"data": {}})
    run_id = cast(str, result["data"]["id"])
    self.run_id = run_id
    logger.info("Created run %s", self.run_id)
    return run_id

  async def _cancel_run(self) -> None:
    """Cancel the current run. Safe to call if no run is active."""
    if self.run_id is None:
      return
    try:
      await self._post(
        f"/runs/{self.run_id}/actions",
        {"data": {"actionType": "stop"}},
      )
    except Exception:
      try:
        await self._delete(f"/runs/{self.run_id}")
      except Exception:
        pass
    self.run_id = None

  # --- Command Execution ---

  async def _execute_command(
    self,
    command_type: str,
    params: Dict[str, Any],
    wait: bool = True,
    timeout: Optional[float] = None,
  ) -> Dict[str, Any]:
    """Execute a command within the current run.

    Commands on the robot are asynchronous: the POST returns
    immediately with status "queued". If ``wait=True`` (default),
    this method polls until the command succeeds or fails.

    Args:
      command_type: e.g., "home", "moveToCoordinates",
        "aspirateInPlace", "pickUpTip", "loadLabware".
      params: Command-specific parameters.
      wait: If True, poll until completion.
      timeout: Max seconds to wait. Defaults to the robot's ``command_timeout``.

    Returns:
      The completed command data dict (includes "result" field).

    Raises:
      OpentronsCommandError: If the command reports status "failed".
      RuntimeError: If the command times out.
    """
    assert self.run_id is not None, "No active run. Call create_run() first."
    if timeout is None:
      timeout = self.command_timeout
    # Headroom rides on a command's own motion time; it is not a floor under a
    # command that has none, or no budget below it could ever be set.
    motion = _plunger_seconds(params)
    if motion > 0:
      timeout = max(timeout, motion + COMMAND_POLL_HEADROOM)
    payload = {
      "data": {
        "commandType": command_type,
        "params": params,
        "intent": "setup",
      }
    }
    result = await self._post(f"/runs/{self.run_id}/commands", payload)
    cmd_data: Dict[str, Any] = result.get("data", {})

    if not wait:
      return cmd_data

    cmd_id = cmd_data.get("id", "")
    if not cmd_id:
      return cmd_data

    # Status is read once more after the deadline passes: a command that finished
    # during the last sleep succeeded, and calling that a timeout aborts a real move.
    deadline = time.monotonic() + timeout
    while True:
      resp = await self._get(f"/runs/{self.run_id}/commands/{cmd_id}")
      cmd_data = resp.get("data", {})
      status = cmd_data.get("status", "")
      if status == "succeeded":
        return cmd_data
      if status == "failed":
        raise OpentronsCommandError(command_type, cmd_data.get("error", {}))
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise RuntimeError(f"Opentrons command '{command_type}' timed out after {timeout}s")
      await asyncio.sleep(min(self.status_poll_interval, remaining))

  # --- Instrument Discovery ---

  async def _get_instruments(self) -> Dict[str, Any]:
    """Query mounted instruments (pipettes, gripper)."""
    return await self._get("/instruments")

  def _parse_pipettes(self, instruments_data: Dict[str, Any]) -> List[PipetteInfo]:
    """Parse the /instruments response into PipetteInfo objects.

    Uses actual data from the API (channels, min_volume, max_volume)
    rather than guessing from pipette names.
    """
    pipettes = []
    for instrument in instruments_data.get("data", []):
      if instrument.get("instrumentType") != "pipette":
        continue
      pip_data = instrument.get("data", {})
      pipettes.append(
        PipetteInfo(
          mount=instrument.get("mount", "unknown"),
          pipette_name=instrument.get("instrumentName", "unknown"),
          pipette_model=instrument.get("instrumentModel", "unknown"),
          pipette_id="",  # set by _load_pipette() later
          channels=pip_data.get("channels", 1),
          min_volume=pip_data.get("min_volume", 1.0),
          max_volume=pip_data.get("max_volume", 1000.0),
        )
      )
    return pipettes

  # --- Pipette Loading ---

  async def _load_pipette(self, pipette_name: str, mount: str) -> str:
    """Load a pipette into the current run.

    Returns the run-scoped pipette ID required by all subsequent
    commands (pickUpTip, aspirateInPlace, moveToCoordinates, etc.).
    Must be called after _create_run().
    """
    result = await self._execute_command(
      "loadPipette",
      {"pipetteName": pipette_name, "mount": mount},
      wait=True,
    )
    pipette_id: str = result.get("result", {}).get("pipetteId", "")
    logger.info(
      "Loaded pipette %s on %s mount -> ID: %s",
      pipette_name,
      mount,
      pipette_id,
    )
    return pipette_id

  # --- Homing ---

  async def home(self) -> Dict[str, Any]:
    """Home all axes. The gantry moves to the rear-left-top."""
    return await self._execute_command("home", {})

  async def _discover_pipette(self) -> PipetteInfo:
    data = await self._get_instruments()
    pipettes = self._parse_pipettes(data)
    if not pipettes:
      raise OpentronsError("No pipette detected", f"{self.host}:{self.port}")
    pip = pipettes[0]
    pip.pipette_id = await self._load_pipette(pip.pipette_name, pip.mount)
    return pip
