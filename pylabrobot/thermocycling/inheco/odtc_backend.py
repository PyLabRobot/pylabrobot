"""ODTC backend implementing ThermocyclerBackend interface using ODTC SiLA interface."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Literal, Optional, Union, cast

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol

from .odtc_sila_interface import (
  DEFAULT_LIFETIME_OF_EXECUTION,
  ODTCSiLAInterface,
  POLLING_START_BUFFER,
  SiLAState,
)
from .odtc_model import (
  ODTCMethod,
  ODTCConfig,
  ODTCMethodSet,
  ODTCPreMethod,
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  ODTCSensorValues,
  ODTCHardwareConstraints,
  StoredProtocol,
  estimate_method_duration_seconds,
  generate_odtc_timestamp,
  get_constraints,
  get_method_by_name,
  list_method_names,
  method_set_to_xml,
  normalize_variant,
  odtc_method_to_protocol,
  parse_method_set,
  parse_method_set_file,
  parse_sensor_values,
  protocol_to_odtc_method,
  resolve_protocol_name,
)

# Buffer (seconds) added to estimated duration for timeout cap (fail faster than full lifetime).
LIFETIME_BUFFER_SECONDS: float = 60.0


def _volume_to_fluid_quantity(volume_ul: float) -> int:
  """Map volume in µL to ODTC fluid_quantity code.

  Args:
    volume_ul: Volume in microliters.

  Returns:
    fluid_quantity code: 0 (10-29ul), 1 (30-74ul), or 2 (75-100ul).

  Raises:
    ValueError: If volume > 100 µL.
  """
  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL. "
      "Please use a volume between 0-100 µL."
    )
  elif volume_ul <= 29:
    return 0  # 10-29ul
  elif volume_ul <= 74:
    return 1  # 30-74ul
  else:  # 75 <= volume_ul <= 100
    return 2  # 75-100ul


def _validate_volume_fluid_quantity(
  volume_ul: float,
  fluid_quantity: int,
  is_premethod: bool = False,
  logger: Optional[logging.Logger] = None,
) -> None:
  """Validate that volume matches fluid_quantity and warn if mismatch.

  Args:
    volume_ul: Volume in microliters.
    fluid_quantity: ODTC fluid_quantity code (0, 1, or 2).
    is_premethod: If True, suppress warnings for volume=0 (premethods don't need volume).
    logger: Logger for warnings (uses module logger if None).
  """
  log = logger or logging.getLogger(__name__)
  if volume_ul <= 0:
    if not is_premethod:
      log.warning(
        f"block_max_volume={volume_ul} µL is invalid. Using default fluid_quantity=1 (30-74ul). "
        "Please provide a valid volume for accurate thermal calibration."
      )
    return

  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL. "
      "Please use a volume between 0-100 µL."
    )

  expected_fluid_quantity = _volume_to_fluid_quantity(volume_ul)
  if fluid_quantity != expected_fluid_quantity:
    volume_ranges = {
      0: "10-29 µL",
      1: "30-74 µL",
      2: "75-100 µL",
    }
    log.warning(
      f"Volume mismatch: block_max_volume={volume_ul} µL suggests fluid_quantity={expected_fluid_quantity} "
      f"({volume_ranges[expected_fluid_quantity]}), but config has fluid_quantity={fluid_quantity} "
      f"({volume_ranges.get(fluid_quantity, 'unknown')}). This may affect thermal calibration accuracy."
    )


@dataclass
class CommandExecution:
  """Handle for an executing async command (SiLA return_code 2).

  Sometimes called a job or task handle in other automation systems.
  Returned from async commands when wait=False. Provides:
  - Awaitable interface (can be awaited like a Task); ``await handle`` and
    ``await handle.wait()`` are equivalent.
  - Request ID access for DataEvent tracking
  - Command completion waiting
  - done, status, estimated_remaining_time, started_at, lifetime for ETA and resumable wait
  """

  request_id: int
  command_name: str
  _future: asyncio.Future[Any]
  backend: "ODTCBackend"
  estimated_remaining_time: Optional[float] = None  # seconds from device duration
  started_at: Optional[float] = None  # time.time() when command was sent
  lifetime: Optional[float] = None  # max wait seconds (for resumable wait)

  def __await__(self):
    """Make this awaitable like a Task."""
    if not self._future.done():
      self._log_wait_info()
    return self._future.__await__()

  @property
  def done(self) -> bool:
    """True if the command has finished (success or error)."""
    return self._future.done()

  @property
  def status(self) -> str:
    """'running', 'success', or 'error'."""
    if not self._future.done():
      return "running"
    try:
      self._future.result()
      return "success"
    except Exception:
      return "error"

  def _log_wait_info(self) -> None:
    """Log command/method name, duration (lifetime), and remaining time (computed at call time).

    Includes a timestamp so log history gives a clear sense of when each wait
    was logged and what remaining time was at that moment, without re-querying.
    """
    import time

    method_name = getattr(self, "method_name", None)
    if isinstance(self, MethodExecution) and method_name:
      name = f"{method_name} ({self.command_name})"
    else:
      name = self.command_name

    lifetime = self.lifetime if self.lifetime is not None else self.backend._get_effective_lifetime()
    started_at = self.started_at if self.started_at is not None else time.time()
    now = time.time()
    elapsed = now - started_at
    remaining = max(0.0, lifetime - elapsed) if lifetime is not None else None

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    msg = f"[{ts}] Waiting for {name}, duration (timeout)={lifetime}s"
    if remaining is not None:
      msg += f", remaining={remaining:.0f}s"
    self.backend.logger.info(msg)

  async def wait(self) -> None:
    """Wait for command completion.

    Equivalent to ``await self`` (the handle is awaitable via __await__).
    """
    if not self._future.done():
      self._log_wait_info()
    await self._future

  async def wait_resumable(self, poll_interval: float = 5.0) -> None:
    """Wait for completion using only GetStatus and handle timing (resumable after restart).

    Use when the in-memory Future is not available (e.g. after process restart).
    Persist the handle (request_id, started_at, estimated_remaining_time, lifetime),
    reconnect the backend, then call this. Uses backend.wait_for_completion_by_time.
    Terminal state is 'idle' for most commands.

    Args:
      poll_interval: Seconds between GetStatus calls.

    Raises:
      TimeoutError: If lifetime exceeded before device reached terminal state.
    """
    import time

    self._log_wait_info()
    started_at = self.started_at if self.started_at is not None else time.time()
    lifetime = self.lifetime if self.lifetime is not None else self.backend._get_effective_lifetime()
    await self.backend.wait_for_completion_by_time(
      request_id=self.request_id,
      started_at=started_at,
      estimated_remaining_time=self.estimated_remaining_time,
      lifetime=lifetime,
      poll_interval=poll_interval,
      terminal_state="idle",
    )

  async def get_data_events(self) -> List[Dict[str, Any]]:
    """Get DataEvents for this command execution.

    Returns:
      List of DataEvent payloads for this request_id.
    """
    events_dict = await self.backend.get_data_events(self.request_id)
    return events_dict.get(self.request_id, [])


@dataclass
class MethodExecution(CommandExecution):
  """Handle for an executing method (SiLA ExecuteMethod; method = runnable protocol).

  Returned from execute_method(wait=False). Provides:
  - All features from CommandExecution (awaitable, request_id, DataEvents)
  - Method-specific status checking
  - Method stopping capability (SiLA: StopMethod)
  """

  method_name: str = ""  # default required after parent's optional fields

  def __post_init__(self):
    """Set command_name to ExecuteMethod for parent class."""
    # Override command_name from parent to be ExecuteMethod
    object.__setattr__(self, 'command_name', "ExecuteMethod")

  async def is_running(self) -> bool:
    """Check if method is still running (checks device busy state).

    Returns:
      True if device state is 'busy', False otherwise.
    """
    return await self.backend.is_method_running()

  async def stop(self) -> None:
    """Stop the currently running method."""
    await self.backend.stop_method()


class ODTCBackend(ThermocyclerBackend):
  """ODTC backend using ODTC-specific SiLA interface.

  Implements ThermocyclerBackend interface for Inheco ODTC devices.
  Uses ODTCSiLAInterface for low-level SiLA communication with parallelism,
  state management, and lockId validation.

  ODTC dimensions for Thermocycler: size_x=147, size_y=298, size_z=130 (mm).
  Construct: backend = ODTCBackend(odtc_ip="...", variant=384000); then
  Thermocycler(name="odtc1", size_x=147, size_y=298, size_z=130, backend=backend, ...).
  """

  def __init__(
    self,
    odtc_ip: str,
    variant: int = 960000,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    poll_interval: float = 5.0,
    lifetime_of_execution: Optional[float] = None,
    on_response_event_missing: Literal["warn_and_continue", "error"] = "warn_and_continue",
  ):
    """Initialize ODTC backend.

    Args:
      odtc_ip: IP address of the ODTC device.
      variant: Well count (96, 384) or ODTC variant code (960000, 384000, 3840000).
        Accepted 96/384 are normalized to 960000/384000. Used for default config
        and constraints (e.g. max slopes, lid temp).
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
      poll_interval: Seconds between GetStatus calls in the async completion polling fallback (SiLA2 subscribe_by_polling style). Default 5.0.
      lifetime_of_execution: Max seconds to wait for async command completion (SiLA2 deadline). If None, uses 3 hours. Protocol execution is always bounded.
      on_response_event_missing: When completion is detected via polling but ResponseEvent was not received: "warn_and_continue" (resolve with None, log warning) or "error" (set exception). Default "warn_and_continue".
    """
    super().__init__()
    self._variant = normalize_variant(variant)
    self._current_execution: Optional[MethodExecution] = None
    self._sila = ODTCSiLAInterface(
      machine_ip=odtc_ip,
      client_ip=client_ip,
      logger=logger,
      poll_interval=poll_interval,
      lifetime_of_execution=lifetime_of_execution,
      on_response_event_missing=on_response_event_missing,
    )
    self.logger = logger or logging.getLogger(__name__)

  @property
  def odtc_ip(self) -> str:
    """IP address of the ODTC device."""
    return self._sila._machine_ip

  @property
  def variant(self) -> int:
    """ODTC variant code (960000 or 384000)."""
    return self._variant

  @property
  def current_execution(self) -> Optional[MethodExecution]:
    """Current method execution handle (set when a method is started with wait=False or wait=True)."""
    return self._current_execution

  def _clear_current_execution_if(self, handle: MethodExecution) -> None:
    """Clear _current_execution only if it still refers to the given handle."""
    if self._current_execution is handle:
      self._current_execution = None

  async def setup(self) -> None:
    """Prepare the ODTC connection and bring the device to idle.

    Performs the full SiLA connection lifecycle:
    1. Sets up the HTTP event receiver server
    2. Calls Reset to move from startup -> standby and register event receiver
    3. Waits for Reset to complete and checks state
    4. Calls Initialize (SiLA command) to move from standby -> idle
    5. Verifies device is in idle state after Initialize

    This is lifecycle/connection setup; initialize() is the SiLA command that
    moves standby -> idle (called by setup() when needed).
    """
    # Step 1: Set up the HTTP event receiver server
    await self._sila.setup()

    # Step 2: Reset (startup -> standby) - registers event receiver URI
    # Reset is async, so we wait for it to complete
    event_receiver_uri = f"http://{self._sila._client_ip}:{self._sila.bound_port}/"
    await self.reset(
      device_id="ODTC",
      event_receiver_uri=event_receiver_uri,
      simulation_mode=False,
    )

    # Step 3: Check state after Reset completes
    # GetStatus is synchronous and will update our internal state tracking
    status = await self.get_status()
    self.logger.info(f"GetStatus returned raw state: {status!r} (type: {type(status).__name__})")

    if status == SiLAState.STANDBY.value:
      self.logger.info("Device is in standby state, calling Initialize...")
      await self.initialize()

      # Step 4: Verify device is in idle state after Initialize
      status_after_init = await self.get_status()

      if status_after_init == SiLAState.IDLE.value:
        self.logger.info("Device successfully initialized and is in idle state")
      else:
        raise RuntimeError(
          f"Device is not in idle state after Initialize. Expected {SiLAState.IDLE.value!r}, "
          f"but got {status_after_init!r}."
        )
    elif status == SiLAState.IDLE.value:
      # Already in idle, nothing to do
      self.logger.info("Device already in idle state after Reset")
    else:
      raise RuntimeError(
        f"Unexpected device state after Reset: {status!r}. Expected {SiLAState.STANDBY.value!r} or {SiLAState.IDLE.value!r}."
      )

  async def stop(self) -> None:
    """Close the ODTC device connection."""
    await self._sila.close()

  def serialize(self) -> dict:
    """Return serialized representation of the backend."""
    return {
      **super().serialize(),
      "odtc_ip": self.odtc_ip,
      "variant": self.variant,
      "port": self._sila.bound_port,
    }

  def _get_effective_lifetime(self) -> float:
    """Effective max wait for async command completion (seconds)."""
    if self._sila._lifetime_of_execution is not None:
      return self._sila._lifetime_of_execution
    return DEFAULT_LIFETIME_OF_EXECUTION

  async def _run_async_command(
    self,
    command_name: str,
    wait: bool,
    execution_class: type,
    method_name: Optional[str] = None,
    estimated_duration_seconds: Optional[float] = None,
    **send_kwargs: Any,
  ) -> Optional[Union[CommandExecution, MethodExecution]]:
    """Run an async SiLA command; return None if wait else execution handle."""
    if wait:
      await self._sila.send_command(command_name, **send_kwargs)
      return None
    fut, request_id, eta, started_at = await self._sila.start_command(
      command_name, estimated_duration_seconds=estimated_duration_seconds, **send_kwargs
    )
    effective = self._get_effective_lifetime()
    if estimated_duration_seconds is not None and estimated_duration_seconds > 0:
      lifetime = min(
        estimated_duration_seconds + LIFETIME_BUFFER_SECONDS,
        effective,
      )
    else:
      lifetime = effective
    if execution_class is MethodExecution:
      return MethodExecution(
        request_id=request_id,
        command_name="ExecuteMethod",
        _future=fut,
        backend=self,
        estimated_remaining_time=eta,
        started_at=started_at,
        lifetime=lifetime,
        method_name=method_name or "",
      )
    return CommandExecution(
      request_id=request_id,
      command_name=command_name,
      _future=fut,
      backend=self,
      estimated_remaining_time=eta,
      started_at=started_at,
      lifetime=lifetime,
    )

  # ============================================================================
  # Response Parsing Utilities
  # ============================================================================

  def _extract_dict_path(
    self, resp: dict, path: List[str], command_name: str, required: bool = True
  ) -> Any:
    """Extract nested value from dict response using path.

    Args:
      resp: Response dict from send_command (SOAP-decoded).
      path: List of keys to traverse (e.g., ["GetStatusResponse", "state"]).
      command_name: Command name for error messages.
      required: If True, raise ValueError if path not found.

    Returns:
      Extracted value, or None if not required and not found.

    Raises:
      ValueError: If required=True and path not found or invalid structure.
    """
    value = resp
    for key in path:
      if not isinstance(value, dict):
        if required:
          raise ValueError(
            f"{command_name}: Expected dict at path {path}, got {type(value).__name__}"
          )
        return None
      value = value.get(key, {})

    if value is None or (isinstance(value, dict) and not value and required):
      if required:
        raise ValueError(
          f"{command_name}: Could not find value at path {path}. Response: {resp}"
        )
      return None
    self.logger.debug(f"{command_name} extracted value at path {path}: {value!r}")
    return value

  def _extract_xml_parameter(
    self, resp: Any, param_name: str, command_name: str, allow_root_fallback: bool = False
  ) -> str:
    """Extract parameter value from ElementTree XML response.

    Args:
      resp: ElementTree root from send_command.
      param_name: Name of parameter to extract (matches 'name' attribute on Parameter element).
      command_name: Command name for error messages.
      allow_root_fallback: If True, fall back to root-based behavior when parameter
        with matching name is not found. If False, raise error if parameter not found.

    Returns:
      Parameter text value.

    Raises:
      ValueError: If response is None or parameter not found.
    """
    if resp is None:
      raise ValueError(f"Empty response from {command_name}")

    import xml.etree.ElementTree as ET

    # First, try strict matching by name attribute
    # Look for Parameter[@name='param_name'] in ResponseData or anywhere in tree
    param = None
    if resp.tag == "Parameter" and resp.get("name") == param_name:
      param = resp
    else:
      # Search for Parameter with matching name attribute
      param = resp.find(f".//Parameter[@name='{param_name}']")

    # Fallback: if not found and fallback allowed, use root-based behavior
    # (for cases where temperature data is in root without name attribute)
    if param is None and allow_root_fallback:
      # Either root is Parameter, or find first Parameter in ResponseData
      param = resp if resp.tag == "Parameter" else resp.find(".//Parameter")

    if param is None:
      # Include full XML structure in error for debugging
      xml_str = ET.tostring(resp, encoding='unicode')
      raise ValueError(
        f"Parameter '{param_name}' not found in {command_name} response. "
        f"Root element tag: {resp.tag}\n"
        f"Full XML response:\n{xml_str}"
      )

    # Extract String element from Parameter (contains escaped XML)
    string_elem = param.find("String")
    if string_elem is None or string_elem.text is None:
      raise ValueError(f"String element not found in {command_name} Parameter response")

    return str(string_elem.text)

  # ============================================================================
  # Basic ODTC Commands
  # ============================================================================

  async def get_status(self) -> str:
    """Get device status state.

    Returns:
      Device state string (e.g., "idle", "busy", "standby").

    Raises:
      ValueError: If response format is unexpected and state cannot be extracted.
    """
    resp = await self._sila.send_command("GetStatus")
    # GetStatus is synchronous - resp is a dict from soap_decode
    # ODTC standard structure: {"GetStatusResponse": {"state": "idle", ...}}
    resp_dict = cast(Dict[str, Any], resp)
    state = self._extract_dict_path(resp_dict, ["GetStatusResponse", "state"], "GetStatus")
    return str(state)

  async def initialize(self, wait: bool = True) -> Optional[CommandExecution]:
    """Initialize the device (SiLA command: standby -> idle).

    Call when device is in standby; setup() performs the full lifecycle
    including Reset and Initialize. SiLA command: Initialize.

    Args:
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    return await self._run_async_command("Initialize", wait, CommandExecution)

  async def reset(
    self,
    device_id: str = "ODTC",
    event_receiver_uri: Optional[str] = None,
    simulation_mode: bool = False,
    wait: bool = True,
  ) -> Optional[CommandExecution]:
    """Reset the device (SiLA command: startup -> standby, register event receiver).

    Args:
      device_id: Device identifier (SiLA: deviceId).
      event_receiver_uri: Event receiver URI (SiLA: eventReceiverURI; auto-detected if None).
      simulation_mode: Enable simulation mode (SiLA: simulationMode).
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    if event_receiver_uri is None:
      event_receiver_uri = f"http://{self._sila._client_ip}:{self._sila.bound_port}/"
    return await self._run_async_command(
      "Reset",
      wait,
      CommandExecution,
      deviceId=device_id,
      eventReceiverURI=event_receiver_uri,
      simulationMode=simulation_mode,
    )

  async def get_device_identification(self) -> dict:
    """Get device identification information.

    Returns:
      Device identification dictionary.
    """
    resp = await self._sila.send_command("GetDeviceIdentification")
    # GetDeviceIdentification is synchronous - resp is a dict from soap_decode
    resp_dict = cast(Dict[str, Any], resp)
    result = self._extract_dict_path(
      resp_dict,
      ["GetDeviceIdentificationResponse", "GetDeviceIdentificationResult"],
      "GetDeviceIdentification",
      required=False,
    )
    return result if isinstance(result, dict) else {}

  async def lock_device(self, lock_id: str, lock_timeout: Optional[float] = None, wait: bool = True) -> Optional[CommandExecution]:
    """Lock the device for exclusive access (SiLA: LockDevice).

    Args:
      lock_id: Unique lock identifier (SiLA: lockId).
      lock_timeout: Lock timeout in seconds (optional; SiLA: lockTimeout).
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    params: dict = {"lockId": lock_id, "PMSId": "PyLabRobot"}
    if lock_timeout is not None:
      params["lockTimeout"] = lock_timeout
    return await self._run_async_command(
      "LockDevice", wait, CommandExecution, lock_id=lock_id, **params
    )

  async def unlock_device(self, wait: bool = True) -> Optional[CommandExecution]:
    """Unlock the device (SiLA: UnlockDevice).

    Args:
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    # Must provide the lockId that was used to lock it
    if self._sila._lock_id is None:
      raise RuntimeError("Device is not locked")
    return await self._run_async_command(
      "UnlockDevice", wait, CommandExecution, lock_id=self._sila._lock_id
    )

  # Door control commands (SiLA: OpenDoor, CloseDoor; thermocycler: lid)
  async def open_door(self, wait: bool = True) -> Optional[CommandExecution]:
    """Open the door (thermocycler lid). SiLA: OpenDoor.

    Args:
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    return await self._run_async_command("OpenDoor", wait, CommandExecution)

  async def close_door(self, wait: bool = True) -> Optional[CommandExecution]:
    """Close the door (thermocycler lid). SiLA: CloseDoor.

    Args:
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    return await self._run_async_command("CloseDoor", wait, CommandExecution)


  # Sensor commands TODO: We cleaned this up at the xml extraction level, clean the method up for temperature reporting
  async def read_temperatures(self) -> ODTCSensorValues:
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    resp = await self._sila.send_command("ReadActualTemperature")

    # Debug logging to see what we actually received
    self.logger.debug(
      f"ReadActualTemperature response type: {type(resp).__name__}, "
      f"isinstance dict: {isinstance(resp, dict)}, "
      f"isinstance ElementTree: {hasattr(resp, 'find') if resp else False}"
    )

    # Handle both synchronous (dict) and asynchronous (ElementTree) responses
    if isinstance(resp, dict):
      # Synchronous response (return_code == 1) - extract from dict structure
      # Structure: ReadActualTemperatureResponse -> ResponseData -> Parameter -> String
      # Parameter might be a dict or list, so we need to find the one with name="SensorValues"
      self.logger.debug(f"ReadActualTemperature dict response keys: {list(resp.keys())}")
      response_data = self._extract_dict_path(
        resp, ["ReadActualTemperatureResponse", "ResponseData"], "ReadActualTemperature"
      )
      self.logger.debug(f"ResponseData structure: {response_data}")

      # Parameter might be a dict or list
      param = response_data.get("Parameter")
      if isinstance(param, list):
        # Find parameter with name="SensorValues"
        sensor_param = next((p for p in param if p.get("name") == "SensorValues"), None)
      elif isinstance(param, dict):
        # Single parameter dict
        sensor_param = param if param.get("name") == "SensorValues" else None
      else:
        sensor_param = None

      if sensor_param is None:
        raise ValueError(
          "SensorValues parameter not found in ReadActualTemperature response"
        )

      sensor_xml = sensor_param.get("String")
      if sensor_xml is None:
        raise ValueError(
          "String element not found in SensorValues parameter"
        )
    else:
      # Asynchronous response (return_code == 2) - resp is ElementTree root
      # Response structure: ResponseData/Parameter[@name='SensorValues']/String
      # Use fallback for temperature data which may be in root without name attribute
      sensor_xml = self._extract_xml_parameter(
        resp, "SensorValues", "ReadActualTemperature", allow_root_fallback=True
      )

    # Parse the XML string (it's escaped in the response)
    return parse_sensor_values(sensor_xml)

  async def get_last_data(self) -> str:
    """Get temperature trace of last executed method (CSV format).

    Returns:
      CSV string with temperature trace data.
    """
    resp = await self._sila.send_command("GetLastData")
    # Response contains CSV data in SiLA Data Capture format
    # For now, return the raw response - parsing can be added later
    return str(resp)  # type: ignore

  # Method control commands (SiLA: ExecuteMethod; method = runnable protocol)
  async def execute_method(
    self,
    method_name: str,
    priority: Optional[int] = None,
    wait: bool = False,
    estimated_duration_seconds: Optional[float] = None,
  ) -> MethodExecution:
    """Execute a method or premethod by name (SiLA: ExecuteMethod; methodName).

    In ODTC/SiLA, a method is a runnable protocol (thermocycling program).
    Always starts the method and returns an execution handle; wait only
    controls whether we await completion before returning.

    Args:
      method_name: Name of the method or premethod to execute (SiLA: methodName).
      priority: Priority (SiLA spec; not used by ODTC).
      wait: If False (default), return handle immediately. If True, block until
          completion then return the (completed) handle.
      estimated_duration_seconds: Optional estimated duration in seconds (used for
          polling timing and timeout; not sent to device).

    Returns:
      MethodExecution handle (completed if wait=True).
    """
    self._current_execution = None
    params: dict = {"methodName": method_name}
    if priority is not None:
      params["priority"] = priority
    handle = await self._run_async_command(
      "ExecuteMethod",
      False,
      MethodExecution,
      method_name=method_name,
      estimated_duration_seconds=estimated_duration_seconds,
      **params,
    )
    assert handle is not None and isinstance(handle, MethodExecution)
    handle._future.add_done_callback(lambda _: self._clear_current_execution_if(handle))
    self._current_execution = handle
    if wait:
      await handle.wait()
    return handle

  async def stop_method(self, wait: bool = True) -> Optional[CommandExecution]:
    """Stop the currently running method (SiLA: StopMethod).

    Args:
      wait: If True, block until completion. If False, return an execution
          handle (CommandExecution).

    Returns:
      If wait=True: None. If wait=False: execution handle (awaitable).
    """
    return await self._run_async_command("StopMethod", wait, CommandExecution)

  async def is_method_running(self) -> bool:
    """Check if a method is currently running.

    Uses GetStatus to check device state. Returns True if state is 'busy',
    indicating a method execution is in progress.

    Returns:
      True if method is running (state is 'busy'), False otherwise.
    """
    status = await self.get_status()
    return status == SiLAState.BUSY.value

  async def wait_for_method_completion(
    self,
    poll_interval: float = 5.0,
    timeout: Optional[float] = None,
  ) -> None:
    """Wait until method execution completes.

    Uses current execution handle (lifetime/eta) when present; otherwise
    polls GetStatus at poll_interval until state returns to 'idle'.

    Args:
      poll_interval: Seconds between status checks (used by handle.wait_resumable
        or fallback poll). Default 5.0.
      timeout: Maximum seconds to wait (fallback poll only). None for no timeout.

    Raises:
      TimeoutError: If timeout is exceeded (fallback poll only).
    """
    import time

    if self._current_execution is not None:
      if self._current_execution.done:
        return
      await self._current_execution.wait_resumable(poll_interval=poll_interval)
      return
    start_time = time.time()
    while await self.is_method_running():
      if timeout is not None:
        elapsed = time.time() - start_time
        if elapsed > timeout:
          raise TimeoutError(
            f"Method execution did not complete within {timeout}s"
          )
      await asyncio.sleep(poll_interval)

  async def wait_for_completion_by_time(
    self,
    request_id: int,
    started_at: float,
    estimated_remaining_time: Optional[float],
    lifetime: float,
    poll_interval: float = 5.0,
    terminal_state: str = "idle",
  ) -> None:
    """Wait for async command completion using only wall-clock and GetStatus (resumable).

    Does not require the in-memory Future. Use after restart: persist request_id,
    started_at, estimated_remaining_time, lifetime from the handle, then call this
    with a reconnected backend.

    (a) Waits until time.time() >= started_at + estimated_remaining_time + buffer.
    (b) Then polls GetStatus every poll_interval until state == terminal_state or
        time.time() - started_at >= lifetime (then raises TimeoutError).

    Args:
      request_id: SiLA request ID (for logging; not used for correlation).
      started_at: time.time() when the command was sent.
      estimated_remaining_time: Device-estimated duration in seconds (or None).
      lifetime: Max seconds to wait (e.g. from handle.lifetime).
      poll_interval: Seconds between GetStatus calls.
      terminal_state: Device state that indicates command finished (default "idle").

    Raises:
      TimeoutError: If lifetime exceeded before terminal state.
    """
    import time

    buffer = POLLING_START_BUFFER
    eta = estimated_remaining_time or 0.0
    while True:
      now = time.time()
      elapsed = now - started_at
      if elapsed >= lifetime:
        raise TimeoutError(
          f"Command (request_id={request_id}) did not complete within {lifetime}s"
        )
      # Don't start polling until estimated time + buffer has passed
      remaining_wait = started_at + eta + buffer - now
      if remaining_wait > 0:
        await asyncio.sleep(min(remaining_wait, poll_interval))
        continue
      status = await self.get_status()
      if status == terminal_state:
        return
      await asyncio.sleep(poll_interval)

  async def get_data_events(self, request_id: Optional[int] = None) -> Dict[int, List[Dict[str, Any]]]:
    """Get collected DataEvents.

    Args:
      request_id: If provided, return events for this request_id only.
          If None, return all collected events.

    Returns:
      Dict mapping request_id to list of DataEvent payloads.
    """
    all_events = self._sila._data_events_by_request_id.copy()

    if request_id is not None:
      return {request_id: all_events.get(request_id, [])}

    return all_events

  async def get_method_set(self) -> ODTCMethodSet:
    """Get the full MethodSet from the device.

    Returns:
      ODTCMethodSet containing all methods and premethods.

    Raises:
      ValueError: If response is empty or MethodsXML parameter not found.
    """
    resp = await self._sila.send_command("GetParameters")
    # Extract MethodsXML parameter
    method_set_xml = self._extract_xml_parameter(resp, "MethodsXML", "GetParameters")
    # Parse MethodSet XML (it's escaped in the response)
    return parse_method_set(method_set_xml)

  async def get_protocol(self, name: str) -> Optional[StoredProtocol]:
    """Get a stored protocol by name (runnable methods only; premethods return None).

    Resolves the stored method by name. If it is a runnable method (ODTCMethod),
    converts it to Protocol + config and returns StoredProtocol. If it is a
    premethod (ODTCPreMethod) or not found, returns None.

    Args:
      name: Protocol name to retrieve.

    Returns:
      StoredProtocol(name, protocol, config) if a runnable method exists, None otherwise.
    """
    method_set = await self.get_method_set()
    resolved = get_method_by_name(method_set, name)
    if resolved is None:
      return None
    if isinstance(resolved, ODTCPreMethod):
      return None
    protocol, config = odtc_method_to_protocol(resolved)
    return StoredProtocol(name=name, protocol=protocol, config=config)

  async def list_protocols(self) -> List[str]:
    """List all protocol names (both methods and premethods) on the device.

    Returns:
      List of protocol names (strings).
    """
    method_set = await self.get_method_set()
    return list_method_names(method_set)

  def get_default_config(self, **kwargs) -> ODTCConfig:
    """Get a default ODTCConfig with variant set to this backend's variant.

    Args:
      **kwargs: Additional parameters to override defaults (e.g. name, lid_temperature).

    Returns:
      ODTCConfig with variant matching this backend (96 or 384-well).
    """
    return ODTCConfig(variant=self._variant, **kwargs)

  def get_constraints(self) -> ODTCHardwareConstraints:
    """Get hardware constraints for this backend's variant.

    Returns:
      ODTCHardwareConstraints for the current variant (96 or 384-well).
    """
    return get_constraints(self._variant)

  async def upload_protocol(
    self,
    protocol: Protocol,
    name: Optional[str] = None,
    config: Optional[ODTCConfig] = None,
    block_max_volume: Optional[float] = None,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> str:
    """Upload a Protocol to the device.

    Args:
      protocol: PyLabRobot Protocol to upload.
      name: Method name. If None, uses scratch name "plr_currentProtocol".
      config: Optional ODTCConfig. If None, uses variant-aware defaults; if
        block_max_volume is provided and in 0–100 µL, sets fluid_quantity from it.
      block_max_volume: Optional volume in µL. If provided and config is None,
        used to set fluid_quantity. If config is provided, validates volume
        matches fluid_quantity.
      allow_overwrite: If False, raise ValueError if method name already exists.
      debug_xml: If True, log generated XML at DEBUG.
      xml_output_path: Optional path to save MethodSet XML.

    Returns:
      Resolved method name (string).

    Raises:
      ValueError: If allow_overwrite=False and method name already exists.
      ValueError: If block_max_volume > 100 µL.
    """
    if config is None:
      if block_max_volume is not None and block_max_volume > 0 and block_max_volume <= 100:
        fluid_quantity = _volume_to_fluid_quantity(block_max_volume)
        config = self.get_default_config(fluid_quantity=fluid_quantity)
      else:
        config = self.get_default_config()
    elif block_max_volume is not None and block_max_volume > 0:
      _validate_volume_fluid_quantity(
        block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
      )

    if name is not None:
      config = replace(config, name=name)

    method = protocol_to_odtc_method(protocol, config=config)
    await self.upload_method(
      method,
      allow_overwrite=allow_overwrite,
      execute=False,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )
    return resolve_protocol_name(method.name)

  async def run_stored_protocol(self, name: str, wait: bool = False, **kwargs) -> MethodExecution:
    """Execute a stored protocol by name (single SiLA ExecuteMethod call).

    No fetch or round-trip; calls the instrument execute-by-name directly.
    Resolves estimated duration from stored method/premethod when available.

    Args:
      name: Name of the stored protocol (method) to run.
      wait: If False (default), start and return handle. If True, block until
          completion then return the (completed) handle.
      **kwargs: Ignored (for API compatibility with base backend).

    Returns:
      MethodExecution handle (completed if wait=True).
    """
    eta: Optional[float] = None
    stored = await self.get_protocol(name)
    if stored is not None:
      method = protocol_to_odtc_method(stored.protocol, config=stored.config)
      eta = estimate_method_duration_seconds(method)
    else:
      method_set = await self.get_method_set()
      resolved = get_method_by_name(method_set, name)
      if isinstance(resolved, ODTCPreMethod):
        eta = PREMETHOD_ESTIMATED_DURATION_SECONDS
    return await self.execute_method(name, wait=wait, estimated_duration_seconds=eta)

  async def upload_method_set(
    self,
    method_set: ODTCMethodSet,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> None:
    """Upload a MethodSet to the device.

    Args:
      method_set: ODTCMethodSet to upload.
      allow_overwrite: If False, raise ValueError if any method/premethod name
        already exists on the device. If True, allow overwriting existing methods/premethods.
      debug_xml: If True, log the generated XML to the logger at DEBUG level.
        Useful for troubleshooting validation errors.
      xml_output_path: Optional file path to save the generated MethodSet XML.
        If provided, the XML will be written to this file before upload.
        Useful for comparing with example XML files or debugging.

    Raises:
      ValueError: If allow_overwrite=False and any method/premethod name already exists
        on the device (checking both methods and premethods for conflicts).
    """
    # Check for name conflicts if overwrite not allowed
    if not allow_overwrite:
      existing_method_set = await self.get_method_set()
      conflicts = []

      # Check all method names (unified search)
      for method in method_set.methods:
        existing_method = get_method_by_name(existing_method_set, method.name)
        if existing_method is not None:
          method_type = "PreMethod" if isinstance(existing_method, ODTCPreMethod) else "Method"
          conflicts.append(f"Method '{method.name}' already exists as {method_type}")

      # Check all premethod names (unified search)
      for premethod in method_set.premethods:
        existing_method = get_method_by_name(existing_method_set, premethod.name)
        if existing_method is not None:
          method_type = "PreMethod" if isinstance(existing_method, ODTCPreMethod) else "Method"
          conflicts.append(f"Method '{premethod.name}' already exists as {method_type}")

      if conflicts:
        conflict_msg = "\n".join(f"  - {c}" for c in conflicts)
        raise ValueError(
          f"Cannot upload MethodSet: name conflicts detected.\n{conflict_msg}\n"
          f"Set allow_overwrite=True to overwrite existing methods."
        )

    method_set_xml = method_set_to_xml(method_set)

    # Debug XML output if requested
    if debug_xml or xml_output_path:
      import xml.dom.minidom
      # Pretty-print for readability
      try:
        dom = xml.dom.minidom.parseString(method_set_xml)
        pretty_xml = dom.toprettyxml(indent="  ")
      except Exception:
        # Fallback to original if pretty-printing fails
        pretty_xml = method_set_xml

      if debug_xml:
        self.logger.debug("Generated MethodSet XML:\n%s", pretty_xml)

      if xml_output_path:
        try:
          with open(xml_output_path, "w", encoding="utf-8") as f:
            f.write(pretty_xml)
          self.logger.info("MethodSet XML saved to: %s", xml_output_path)
        except Exception as e:
          self.logger.warning("Failed to save XML to %s: %s", xml_output_path, e)

    # SetParameters expects paramsXML in ResponseType_1.2.xsd format
    # Format: <ParameterSet><Parameter parameterType="String" name="MethodsXML"><String>...</String></Parameter></ParameterSet>
    import xml.etree.ElementTree as ET
    param_set = ET.Element("ParameterSet")
    param = ET.SubElement(param_set, "Parameter", parameterType="String", name="MethodsXML")
    string_elem = ET.SubElement(param, "String")
    # XML needs to be escaped for embedding in another XML
    string_elem.text = method_set_xml

    params_xml = ET.tostring(param_set, encoding="unicode", xml_declaration=False)

    if debug_xml:
      self.logger.debug("Wrapped ParameterSet XML (sent to device):\n%s", params_xml)

    await self._sila.send_command("SetParameters", paramsXML=params_xml)

  async def upload_method(
    self,
    method: ODTCMethod,
    allow_overwrite: bool = False,
    execute: bool = False,
    wait: bool = True,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> Optional[MethodExecution]:
    """Upload a single method to the device.

    Convenience wrapper that wraps method in MethodSet and uploads.

    Args:
      method: ODTCMethod to upload.
      allow_overwrite: If False, raise ValueError if method name already exists
        on the device. If True, allow overwriting existing method/premethod.
        If method name resolves to scratch name and this is not explicitly False,
        it will be set to True automatically.
      execute: If True, execute the method after uploading. If False, only upload.
      wait: If execute=True and wait=True, block until method completes.
        If execute=True and wait=False, return MethodExecution handle.
      debug_xml: If True, log the generated XML to the logger at DEBUG level.
        Passed through to upload_method_set.
      xml_output_path: Optional file path to save the generated MethodSet XML.
        Passed through to upload_method_set.

    Returns:
      If execute=False: None
      If execute=True and wait=True: None (blocks until complete)
      If execute=True and wait=False: MethodExecution handle (awaitable, has request_id)

    Raises:
      ValueError: If allow_overwrite=False and method name already exists
        on the device (checking both methods and premethods for conflicts).
    """
    # Resolve name (use scratch name if None/empty)
    resolved_name = resolve_protocol_name(method.name)
    # Check if we're using a scratch name (original name was None/empty)
    is_scratch_name = not method.name or method.name == ""

    # Generate timestamp if not already set
    resolved_datetime = method.datetime if method.datetime else generate_odtc_timestamp()

    # Auto-overwrite for scratch names unless explicitly disabled
    if is_scratch_name and allow_overwrite is False:
      # Check if user explicitly passed False (vs default)
      # Since we can't distinguish, we'll auto-overwrite for scratch names
      # but log a warning if they explicitly set False
      allow_overwrite = True
      if not method.name:  # Only warn if name was actually None/empty (not just resolved)
        self.logger.warning(
          f"Method name resolved to scratch name '{resolved_name}'. "
          "Auto-enabling allow_overwrite=True for scratch methods."
        )

    # Create method copy with resolved name and timestamp
    method_copy = ODTCMethod(
      name=resolved_name,
      variant=method.variant,
      plate_type=method.plate_type,
      fluid_quantity=method.fluid_quantity,
      post_heating=method.post_heating,
      start_block_temperature=method.start_block_temperature,
      start_lid_temperature=method.start_lid_temperature,
      steps=method.steps,
      pid_set=method.pid_set,
      creator=method.creator,
      description=method.description,
      datetime=resolved_datetime,
    )

    method_set = ODTCMethodSet(methods=[method_copy], premethods=[])
    await self.upload_method_set(
      method_set,
      allow_overwrite=allow_overwrite,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

    if execute:
      return await self.execute_method(resolved_name, wait=wait)
    return None

  async def upload_premethod(
    self,
    premethod: ODTCPreMethod,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> None:
    """Upload a single premethod to the device.

    Convenience wrapper that wraps premethod in MethodSet and uploads.

    Args:
      premethod: ODTCPreMethod to upload.
      allow_overwrite: If False, raise ValueError if premethod name already exists
        on the device. If True, allow overwriting existing method/premethod.
        If premethod name resolves to scratch name and this is not explicitly False,
        it will be set to True automatically.

    Raises:
      ValueError: If allow_overwrite=False and premethod name already exists
        on the device (checking both methods and premethods for conflicts).
    """
    # Resolve name (use scratch name if None/empty)
    resolved_name = resolve_protocol_name(premethod.name)
    # Check if we're using a scratch name (original name was None/empty)
    is_scratch_name = not premethod.name or premethod.name == ""

    # Generate timestamp if not already set
    resolved_datetime = premethod.datetime if premethod.datetime else generate_odtc_timestamp()

    # Auto-overwrite for scratch names unless explicitly disabled
    if is_scratch_name and allow_overwrite is False:
      # Check if user explicitly passed False (vs default)
      # Since we can't distinguish, we'll auto-overwrite for scratch names
      # but log a warning if they explicitly set False
      allow_overwrite = True
      if not premethod.name:  # Only warn if name was actually None/empty (not just resolved)
        self.logger.warning(
          f"PreMethod name resolved to scratch name '{resolved_name}'. "
          "Auto-enabling allow_overwrite=True for scratch premethods."
        )

    # Create premethod copy with resolved name and timestamp
    premethod_copy = ODTCPreMethod(
      name=resolved_name,
      target_block_temperature=premethod.target_block_temperature,
      target_lid_temperature=premethod.target_lid_temperature,
      creator=premethod.creator,
      description=premethod.description,
      datetime=resolved_datetime,
    )

    method_set = ODTCMethodSet(methods=[], premethods=[premethod_copy])
    await self.upload_method_set(
      method_set,
      allow_overwrite=allow_overwrite,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

  async def upload_method_set_from_file(
    self,
    filepath: str,
    allow_overwrite: bool = False,
  ) -> None:
    """Load and upload a MethodSet XML file to the device.

    Args:
      filepath: Path to MethodSet XML file.
      allow_overwrite: If False, raise ValueError if any method/premethod name
        already exists on the device. If True, allow overwriting existing methods/premethods.

    Raises:
      ValueError: If allow_overwrite=False and any method/premethod name already exists
        on the device (checking both methods and premethods for conflicts).
    """
    method_set = parse_method_set_file(filepath)
    await self.upload_method_set(method_set, allow_overwrite=allow_overwrite)

  async def save_method_set_to_file(self, filepath: str) -> None:
    """Download methods from device and save to file.

    Args:
      filepath: Path to save MethodSet XML file.
    """
    resp = await self._sila.send_command("GetParameters")
    # Extract MethodsXML parameter
    method_set_xml = self._extract_xml_parameter(resp, "MethodsXML", "GetParameters")
    # XML is escaped in the response, so we get it as-is
    # Write to file
    with open(filepath, "w", encoding="utf-8") as f:
      f.write(method_set_xml)

  # ============================================================================
  # ThermocyclerBackend Abstract Methods
  # ============================================================================

  async def open_lid(self, wait: bool = True, **kwargs: Any):
    """Open the thermocycler lid (ODTC SiLA: OpenDoor)."""
    return await self.open_door(wait=wait)

  async def close_lid(self, wait: bool = True, **kwargs: Any):
    """Close the thermocycler lid (ODTC SiLA: CloseDoor)."""
    return await self.close_door(wait=wait)

  async def set_block_temperature(
    self,
    temperature: List[float],
    lid_temperature: Optional[float] = None,
    wait: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
    **kwargs: Any,
  ):
    """Set block (mount) temperature and hold it via PreMethod.

    ODTC has no direct SetBlockTemperature command; this creates and runs a
    PreMethod to set block and lid temperatures.

    Args:
      temperature: Target block temperature(s) in °C (ODTC single zone: use temperature[0]).
      lid_temperature: Optional lid temperature in °C. If None, uses hardware max_lid_temp.
      wait: If True, block until set. If False (default), return MethodExecution handle.
      debug_xml: If True, log generated XML at DEBUG.
      xml_output_path: Optional path to save MethodSet XML.
      **kwargs: Ignored (for API compatibility).

    Returns:
      If wait=True: None. If wait=False: MethodExecution handle.
    """
    if not temperature:
      raise ValueError("At least one block temperature required")
    block_temp = temperature[0]
    if lid_temperature is not None:
      target_lid_temp = lid_temperature
    else:
      constraints = self.get_constraints()
      target_lid_temp = constraints.max_lid_temp

    resolved_name = resolve_protocol_name(None)
    premethod = ODTCPreMethod(
      name=resolved_name,
      target_block_temperature=block_temp,
      target_lid_temperature=target_lid_temp,
      datetime=generate_odtc_timestamp(),
    )
    await self.upload_premethod(
      premethod,
      allow_overwrite=True,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )
    return await self.execute_method(
      resolved_name,
      wait=wait,
      estimated_duration_seconds=PREMETHOD_ESTIMATED_DURATION_SECONDS,
    )

  async def set_lid_temperature(self, temperature: List[float]) -> None:
    """Set lid temperature.

    ODTC does not have a direct SetLidTemperature command; lid temperature is
    set per protocol (ODTCConfig.lid_temperature) or inside a Method. Use
    run_protocol() or run_stored_protocol() instead of run_pcr_profile().

    Args:
      temperature: Target temperature(s) in °C.

    Raises:
      NotImplementedError: ODTC does not support direct lid temperature setting.
    """
    raise NotImplementedError(
      "ODTC does not support set_lid_temperature; lid temperature is set per "
      "protocol or via ODTCConfig. Use run_protocol() or run_stored_protocol() "
      "instead of run_pcr_profile()."
    )

  async def deactivate_block(self) -> None:
    """Deactivate block (maps to StopMethod)."""
    await self.stop_method()

  async def deactivate_lid(self) -> None:
    """Deactivate lid (maps to StopMethod)."""
    await self.stop_method()

  async def run_protocol(
    self,
    protocol: Protocol,
    block_max_volume: float,
    **kwargs: Any,
  ) -> MethodExecution:
    """Execute thermocycler protocol (convert, upload, execute).

    Converts Protocol to ODTCMethod, uploads it, then executes by name.
    Always returns immediately with a MethodExecution handle; to block until
    completion, await handle.wait() or use wait_for_profile_completion().
    Config is derived from block_max_volume and backend variant if not provided.

    Args:
      protocol: Protocol to execute.
      block_max_volume: Maximum block volume (µL) for safety; used to set
        fluid_quantity when config is None.
      **kwargs: Backend-specific options. ODTC accepts ``config`` (ODTCConfig,
        optional); if omitted, built from block_max_volume and variant.

    Returns:
      MethodExecution handle. Caller can await handle.wait() or
      wait_for_profile_completion() to block until done.
    """
    config = kwargs.pop("config", None)
    if config is None:
      if block_max_volume > 0 and block_max_volume <= 100:
        fluid_quantity = _volume_to_fluid_quantity(block_max_volume)
        config = self.get_default_config(fluid_quantity=fluid_quantity)
      else:
        config = self.get_default_config()
      if block_max_volume > 0 and block_max_volume <= 100:
        _validate_volume_fluid_quantity(
          block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
        )
    else:
      if block_max_volume > 0:
        _validate_volume_fluid_quantity(
          block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
        )

    method = protocol_to_odtc_method(protocol, config=config)
    await self.upload_method(method, allow_overwrite=True, execute=False)
    resolved_name = resolve_protocol_name(method.name)
    eta = estimate_method_duration_seconds(method)
    return await self.execute_method(
      resolved_name, wait=False, estimated_duration_seconds=eta
    )

  async def get_block_current_temperature(self) -> List[float]:
    """Get current block temperature.

    Returns:
      List of block temperatures in °C (single zone for ODTC).
    """
    sensor_values = await self.read_temperatures()
    return [sensor_values.mount]

  async def get_block_target_temperature(self) -> List[float]:
    """Get block target temperature.

    Returns:
      List of target temperatures in °C.

    Raises:
      RuntimeError: If no target is set.
    """
    # ODTC does not expose block target temperature; use is_method_running() or
    # wait_for_method_completion() to monitor execution.
    raise RuntimeError(
      "ODTC does not report block target temperature; method execution state "
      "not tracked. Use backend.is_method_running() or wait_for_method_completion() "
      "to monitor execution."
    )

  async def get_lid_current_temperature(self) -> List[float]:
    """Get current lid temperature.

    Returns:
      List of lid temperatures in °C (single zone for ODTC).
    """
    sensor_values = await self.read_temperatures()
    return [sensor_values.lid]

  async def get_lid_target_temperature(self) -> List[float]:
    """Get lid target temperature.

    Returns:
      List of target temperatures in °C.

    Raises:
      RuntimeError: If no target is set.
    """
    # ODTC does not expose lid target temperature; use is_method_running() or
    # wait_for_method_completion() to monitor execution.
    raise RuntimeError(
      "ODTC does not report lid target temperature; method execution state "
      "not tracked. Use backend.is_method_running() or wait_for_method_completion() "
      "to monitor execution."
    )

  async def get_lid_open(self) -> bool:
    """Check if lid is open.

    ODTC does not expose door open/closed state. Use open_door()/close_door() to
    control the door; there is no query for current state.

    Returns:
      True if lid/door is open.

    Raises:
      NotImplementedError: ODTC does not support querying lid/door open state.
    """
    raise NotImplementedError(
      "ODTC does not support get_lid_open; door status is not reported. Use "
      "open_door() or close_door() to control the door."
    )

  async def get_lid_status(self) -> LidStatus:
    """Get lid temperature status.

    Returns:
      LidStatus enum value.
    """
    # Simplified: if we can read temperature, assume it's holding
    try:
      await self.read_temperatures()
      return LidStatus.HOLDING_AT_TARGET
    except Exception:
      return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    """Get block temperature status.

    Returns:
      BlockStatus enum value.
    """
    # Simplified: if we can read temperature, assume it's holding
    try:
      await self.read_temperatures()
      return BlockStatus.HOLDING_AT_TARGET
    except Exception:
      return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    """Get remaining hold time.

    ODTC does not report per-step hold time. Use is_method_running() or
    wait_for_method_completion() to monitor execution.

    Returns:
      Remaining hold time in seconds.

    Raises:
      NotImplementedError: ODTC does not report hold time.
    """
    raise NotImplementedError(
      "ODTC does not report remaining hold time; method execution state not "
      "tracked. Use is_method_running() or wait_for_method_completion() to "
      "monitor execution."
    )

  async def get_current_cycle_index(self) -> int:
    """Get current cycle index.

    ODTC does not report cycle/step indices. Use is_method_running() or
    wait_for_method_completion() to monitor execution.

    Returns:
      Zero-based cycle index.

    Raises:
      NotImplementedError: ODTC does not report cycle index.
    """
    raise NotImplementedError(
      "ODTC does not report current cycle index; method execution state not "
      "tracked. Use is_method_running() or wait_for_method_completion() to "
      "monitor execution."
    )

  async def get_total_cycle_count(self) -> int:
    """Get total cycle count.

    ODTC does not report cycle/step counts. Use is_method_running() or
    wait_for_method_completion() to monitor execution.

    Returns:
      Total number of cycles.

    Raises:
      NotImplementedError: ODTC does not report total cycle count.
    """
    raise NotImplementedError(
      "ODTC does not report total cycle count; method execution state not "
      "tracked. Use is_method_running() or wait_for_method_completion() to "
      "monitor execution."
    )

  async def get_current_step_index(self) -> int:
    """Get current step index.

    ODTC does not report cycle/step indices. Use is_method_running() or
    wait_for_method_completion() to monitor execution.

    Returns:
      Zero-based step index.

    Raises:
      NotImplementedError: ODTC does not report current step index.
    """
    raise NotImplementedError(
      "ODTC does not report current step index; method execution state not "
      "tracked. Use is_method_running() or wait_for_method_completion() to "
      "monitor execution."
    )

  async def get_total_step_count(self) -> int:
    """Get total step count.

    ODTC does not report cycle/step counts. Use is_method_running() or
    wait_for_method_completion() to monitor execution.

    Returns:
      Total number of steps.

    Raises:
      NotImplementedError: ODTC does not report total step count.
    """
    raise NotImplementedError(
      "ODTC does not report total step count; method execution state not "
      "tracked. Use is_method_running() or wait_for_method_completion() to "
      "monitor execution."
    )
