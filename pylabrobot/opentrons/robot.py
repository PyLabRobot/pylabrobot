import abc
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from pylabrobot.opentrons.transport import HttpxTransport, OpentronsTransport

logger = logging.getLogger(__name__)


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
  ) -> None:
    self.host, self.port = host, port
    self.base_url = f"http://{host}:{port}"
    self._transport: Optional[OpentronsTransport] = transport
    self.run_id: Optional[str] = None
    self.pipette: Optional[PipetteInfo] = None
    self.api_version: Optional[str] = None
    self.robot_model: Optional[str] = None

  async def setup(self) -> None:
    await self._connect()
    await self._create_run()
    await self._model_setup()

  async def stop(self) -> None:
    # Always home before releasing the robot so the gantry parks in a known
    # pose. Done inside the run (before cancel); a failure here must not block
    # disconnect.
    try:
      await self.home()
    except Exception:
      logger.warning("home() before stop failed; continuing to disconnect", exc_info=True)
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
    """Create the transport (unless one was injected) and verify connectivity.

    Sends a health check to confirm the robot is reachable and the robot
    server is running (not in Jupyter/Python API mode).
    """
    if self._transport is None:
      self._transport = HttpxTransport(base_url=self.base_url)
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
    """Close the transport."""
    if self._transport is not None:
      await self._transport.close()
      self._transport = None

  # --- Low-Level Wire Calls ---

  async def _get(self, path: str) -> Dict[str, Any]:
    """Wire GET, return parsed JSON."""
    assert self._transport is not None, "Not connected. Call connect() first."
    return await self._transport.get(path)

  async def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Wire POST, return parsed JSON."""
    assert self._transport is not None, "Not connected. Call connect() first."
    return await self._transport.post(path, json=data or {})

  async def _delete(self, path: str) -> Dict[str, Any]:
    """Wire DELETE, return parsed JSON."""
    assert self._transport is not None, "Not connected. Call connect() first."
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
    timeout: float = 30.0,
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
      timeout: Max seconds to wait.

    Returns:
      The completed command data dict (includes "result" field).

    Raises:
      OpentronsCommandError: If the command reports status "failed".
      RuntimeError: If the command times out.
    """
    assert self.run_id is not None, "No active run. Call create_run() first."
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

    # Poll for completion
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      resp = await self._get(f"/runs/{self.run_id}/commands/{cmd_id}")
      cmd_data = resp.get("data", {})
      status = cmd_data.get("status", "")
      if status == "succeeded":
        return cmd_data
      elif status == "failed":
        raise OpentronsCommandError(command_type, cmd_data.get("error", {}))
      await asyncio.sleep(0.2)

    raise RuntimeError(f"Opentrons command '{command_type}' timed out after {timeout}s")

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
