import abc
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

try:
  import httpx
  _HAS_HTTPX = True
except ImportError:
  _HAS_HTTPX = False

logger = logging.getLogger(__name__)


class OpentronsError(Exception):
  def __init__(self, title: str, message: Optional[str] = None) -> None:
    self.title, self.message = title, message
    super().__init__(f"{title}: {message}" if message else title)


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

  Owns the httpx transport, the run/command protocol, and instrument discovery.
  Subclasses implement the liquid-handling ops and any model-specific setup.
  """

  def __init__(self, host: str, port: int = 31950) -> None:
    if not _HAS_HTTPX:
      raise RuntimeError("httpx is required. Install with: pip install httpx")
    self.host, self.port = host, port
    self.base_url = f"http://{host}:{port}"
    self._client: Optional["httpx.AsyncClient"] = None
    self.run_id: Optional[str] = None
    self.pipette: Optional[PipetteInfo] = None
    self.api_version: Optional[str] = None
    self.robot_model: Optional[str] = None

  async def setup(self) -> None:
    logger.warning(
      "OpentronsRobot has not been verified against real hardware; use with care "
      "and report success so this warning can be removed."
    )
    await self._connect()
    await self._create_run()
    self.pipette = await self._discover_pipette()
    await self._model_setup()

  async def stop(self) -> None:
    await self._cancel_run()
    await self._disconnect()

  @abc.abstractmethod
  async def _model_setup(self) -> None:
    """Model-specific post-connection setup (home, load pipette id, etc.)."""

  # --- Connection Lifecycle ---

  async def _connect(self) -> None:
    """Create HTTP session and verify connectivity.

    Sends a health check to confirm the robot is reachable and the robot
    server is running (not in Jupyter/Python API mode).
    """
    self._client = httpx.AsyncClient(
      base_url=self.base_url,
      timeout=30.0,
      headers={"opentrons-version": "3"},
    )
    health = await self._get("/health")
    self.api_version = health.get("api_version")
    self.robot_model = health.get("robot_model", "")
    robot_name = health.get("name", "unknown")
    logger.info(
      "Connected to robot '%s' at %s:%s (API %s, model: %s)",
      robot_name, self.host, self.port,
      self.api_version, self.robot_model,
    )

  async def _disconnect(self) -> None:
    """Close the HTTP session."""
    if self._client is not None:
      await self._client.aclose()
      self._client = None

  # --- Low-Level HTTP ---

  async def _get(self, path: str) -> Dict[str, Any]:
    """HTTP GET, return parsed JSON."""
    assert self._client is not None, "Not connected. Call connect() first."
    response = await self._client.get(path)
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

  async def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """HTTP POST, return parsed JSON."""
    assert self._client is not None, "Not connected. Call connect() first."
    response = await self._client.post(path, json=data or {})
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

  async def _delete(self, path: str) -> Dict[str, Any]:
    """HTTP DELETE, return parsed JSON."""
    assert self._client is not None, "Not connected. Call connect() first."
    response = await self._client.delete(path)
    response.raise_for_status()
    return cast(Dict[str, Any], response.json())

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

  async def execute_command(
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
      RuntimeError: If the command fails or times out.
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
        error = cmd_data.get("error", {})
        raise RuntimeError(
          f"Opentrons command '{command_type}' failed: "
          f"{error.get('detail', error)}"
        )
      await asyncio.sleep(0.2)

    raise RuntimeError(
      f"Opentrons command '{command_type}' timed out after {timeout}s"
    )

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

  def check_gripper(self, instruments_data: Dict[str, Any]) -> bool:
    """Check if a gripper is attached."""
    for instrument in instruments_data.get("data", []):
      if instrument.get("instrumentType") == "gripper":
        return True
    return False

  async def get_modules(self) -> List[Dict[str, Any]]:
    """Query connected modules via GET /modules.

    Returns the module data dict for each USB-connected module. Passive
    hardware (magnetic block, waste chute) is not discoverable.
    """
    data = await self._get("/modules")
    return cast(List[Dict[str, Any]], data.get("data", []))

  # --- Pipette Loading ---

  async def _load_pipette(self, pipette_name: str, mount: str) -> str:
    """Load a pipette into the current run.

    Returns the run-scoped pipette ID required by all subsequent
    commands (pickUpTip, aspirateInPlace, moveToCoordinates, etc.).
    Must be called after _create_run().
    """
    result = await self.execute_command(
      "loadPipette",
      {"pipetteName": pipette_name, "mount": mount},
      wait=True,
    )
    pipette_id: str = result.get("result", {}).get("pipetteId", "")
    logger.info(
      "Loaded pipette %s on %s mount -> ID: %s",
      pipette_name, mount, pipette_id,
    )
    return pipette_id

  # --- Homing ---

  async def home(self) -> Dict[str, Any]:
    """Home all axes. The gantry moves to the rear-left-top."""
    return await self.execute_command("home", {})

  # --- Movement Commands ---

  async def move_to_coordinates(
    self,
    pipette_id: str,
    x: float,
    y: float,
    z: float,
    minimum_z_height: Optional[float] = None,
    speed: Optional[float] = None,
  ) -> Dict[str, Any]:
    """Move a pipette to absolute deck coordinates.

    The robot automatically arcs to a safe Z height before lateral
    movement (unless forceDirect=True, which we never set).
    The minimumZHeight parameter raises the arc if needed.
    """
    params: Dict[str, Any] = {
      "pipetteId": pipette_id,
      "coordinates": {"x": x, "y": y, "z": z},
    }
    if minimum_z_height is not None:
      params["minimumZHeight"] = minimum_z_height
    if speed is not None:
      params["speed"] = speed
    return await self.execute_command("moveToCoordinates", params)

  async def _discover_pipette(self) -> PipetteInfo:
    data = await self._get_instruments()
    pipettes = self._parse_pipettes(data)
    if not pipettes:
      raise OpentronsError("No pipette detected", f"{self.host}:{self.port}")
    pip = pipettes[0]
    pip.pipette_id = await self._load_pipette(pip.pipette_name, pip.mount)
    return pip
