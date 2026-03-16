"""ODTC backend implementing ThermocyclerBackend interface using ODTC SiLA interface."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol

from .odtc_model import (
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  ODTCConfig,
  ODTCHardwareConstraints,
  ODTCMethodSet,
  ODTCProgress,
  ODTCProtocol,
  ODTCSensorValues,
  ProtocolList,
  estimate_method_duration_seconds,
  generate_odtc_timestamp,
  get_constraints,
  get_method_by_name,
  method_set_to_xml,
  normalize_variant,
  odtc_protocol_to_protocol,
  parse_method_set,
  parse_method_set_file,
  parse_sensor_values,
  protocol_to_odtc_protocol,
  validate_volume_fluid_quantity,
  volume_to_fluid_quantity,
)
from pylabrobot.storage.inheco.scila.inheco_sila_interface import SiLAState

from .odtc_sila_interface import (
  DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS,
  DEFAULT_LIFETIME_OF_EXECUTION,
  POLLING_START_BUFFER,
  ODTCSiLAInterface,
)

# Buffer (seconds) added to device remaining duration (ExecuteMethod) or first_event_timeout (status commands) for timeout cap (fail faster than full lifetime).
LIFETIME_BUFFER_SECONDS: float = 60.0


class ODTCCommand(str, Enum):
  INITIALIZE = "Initialize"
  RESET = "Reset"
  LOCK_DEVICE = "LockDevice"
  UNLOCK_DEVICE = "UnlockDevice"
  OPEN_DOOR = "OpenDoor"
  CLOSE_DOOR = "CloseDoor"
  STOP_METHOD = "StopMethod"
  EXECUTE_METHOD = "ExecuteMethod"


# =============================================================================
# SiLA Response Normalization (single abstraction for dict or ET responses)
# =============================================================================


class _NormalizedSiLAResponse:
  """Normalized result of a SiLA command (sync dict or async ElementTree).

  Used only by ODTCBackend. Build via from_raw(); then get_value() for
  dict-path extraction or get_parameter_string() for Parameter/String.
  """

  def __init__(
    self,
    command: str,
    _dict: Optional[Dict[str, Any]] = None,
    _et_root: Optional[ET.Element] = None,
  ) -> None:
    self._command = command
    self._dict = _dict
    self._et_root = _et_root
    if _dict is not None and _et_root is not None:
      raise ValueError("_NormalizedSiLAResponse: provide _dict or _et_root, not both")
    if _dict is None and _et_root is None:
      raise ValueError("_NormalizedSiLAResponse: provide _dict or _et_root")

  @classmethod
  def from_raw(
    cls,
    raw: Union[Dict[str, Any], ET.Element, None],
    command: str,
  ) -> "_NormalizedSiLAResponse":
    """Build from send_command return value (dict for sync, ET root for async)."""
    if raw is None:
      return cls(command=command, _dict={})
    if isinstance(raw, dict):
      return cls(command=command, _dict=raw)
    return cls(command=command, _et_root=raw)

  def get_value(self, *path: str, required: bool = True) -> Any:
    """Get nested value from dict response by key path. Only for dict (sync) responses."""
    if self._dict is None:
      raise ValueError(f"{self._command}: get_value() only supported for dict (sync) responses")
    value: Any = self._dict
    path_list = list(path)
    for key in path_list:
      if not isinstance(value, dict):
        if required:
          raise ValueError(
            f"{self._command}: Expected dict at path {path_list}, got {type(value).__name__}"
          )
        return None
      value = value.get(key, {})

    if value is None or (isinstance(value, dict) and not value and required):
      if required:
        raise ValueError(
          f"{self._command}: Could not find value at path {path_list}. Response: {self._dict}"
        )
      return None
    return value

  def get_parameter_string(
    self,
    name: str,
    allow_root_fallback: bool = False,
  ) -> str:
    """Get Parameter[@name=name]/String value (dict or ET response)."""
    if self._dict is not None:
      response_data_path: List[str] = [
        f"{self._command}Response",
        "ResponseData",
      ]
      response_data = self._get_dict_path(response_data_path, required=True)
      param = response_data.get("Parameter")
      if isinstance(param, list):
        found = next((p for p in param if p.get("name") == name), None)
      elif isinstance(param, dict):
        found = param if param.get("name") == name else None
      else:
        found = None
      if found is None:
        raise ValueError(f"Parameter '{name}' not found in {self._command} response")
      value = found.get("String")
      if value is None:
        raise ValueError(f"String element not found in {name} parameter")
      return str(value)

    resp = self._et_root
    if resp is None:
      raise ValueError(f"Empty response from {self._command}")

    param = None
    if resp.tag == "Parameter" and resp.get("name") == name:
      param = resp
    else:
      param = resp.find(f".//Parameter[@name='{name}']")

    if param is None and allow_root_fallback:
      param = resp if resp.tag == "Parameter" else resp.find(".//Parameter")

    if param is None:
      xml_str = ET.tostring(resp, encoding="unicode")
      raise ValueError(
        f"Parameter '{name}' not found in {self._command} response. "
        f"Root element tag: {resp.tag}\nFull XML response:\n{xml_str}"
      )

    string_elem = param.find("String")
    if string_elem is None or string_elem.text is None:
      raise ValueError(f"String element not found in {self._command} Parameter response")
    return str(string_elem.text)

  def _get_dict_path(self, path: List[str], required: bool = True) -> Any:
    """Internal: traverse dict by path."""
    if self._dict is None:
      if required:
        raise ValueError(f"{self._command}: response is not dict")
      return None
    value: Any = self._dict
    for key in path:
      if not isinstance(value, dict):
        if required:
          raise ValueError(
            f"{self._command}: Expected dict at path {path}, got {type(value).__name__}"
          )
        return None
      value = value.get(key, {})
    if value is None or (isinstance(value, dict) and not value and required):
      if required:
        raise ValueError(f"{self._command}: Could not find value at path {path}")
      return None
    return value

  def raw(self) -> Union[Dict[str, Any], ET.Element]:
    """Return the underlying dict or ET root (e.g. for GetLastData)."""
    if self._dict is not None:
      return self._dict
    if self._et_root is not None:
      return self._et_root
    return {}


@dataclass
class ODTCExecution:
  """Handle for an executing async command (SiLA return_code 2). Returned when wait=False.

  Provides: awaitable interface, request_id, done/status, wait/wait_resumable,
  get_data_events. For ExecuteMethod: method_name, is_running(), stop().
  """

  request_id: int
  command: str
  _future: asyncio.Future[Any]
  backend: "ODTCBackend"
  estimated_remaining_time: Optional[float] = None
  started_at: Optional[float] = None
  lifetime: Optional[float] = None
  method_name: Optional[str] = None  # set for ExecuteMethod

  def __await__(self):
    return self.wait().__await__()

  @property
  def done(self) -> bool:
    return self._future.done()

  @property
  def status(self) -> str:
    if not self._future.done():
      return "running"
    try:
      self._future.result()
      return "success"
    except Exception:
      return "error"

  def _log_wait_info(self) -> None:
    import time

    name = f"{self.method_name} ({self.command})" if self.method_name else self.command
    lifetime = (
      self.lifetime if self.lifetime is not None else self.backend._get_effective_lifetime()
    )
    started_at = self.started_at if self.started_at is not None else time.time()
    remaining = max(0.0, lifetime - (time.time() - started_at)) if lifetime is not None else None
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines = [
      f"[{ts}] Waiting for command",
      f"  Command: {name}",
      f"  Duration (timeout): {lifetime}s",
    ]
    if remaining is not None:
      lines.append(f"  Remaining: {remaining:.0f}s")
    self.backend.logger.info("\n".join(lines))

  async def _is_done(self) -> bool:
    return self._future.done()

  async def wait(self) -> None:
    if not self._future.done():
      self._log_wait_info()
    interval = self.backend.progress_log_interval
    if interval and interval > 0:
      task = asyncio.create_task(
        self.backend._run_progress_loop_until(
          self.request_id,
          interval,
          self._is_done,
          self.backend.progress_callback,
        )
      )
      try:
        await self._future
      finally:
        task.cancel()
        try:
          await task
        except asyncio.CancelledError:
          pass
    else:
      await self._future

  async def wait_resumable(self, poll_interval: float = 5.0) -> None:
    import time

    self._log_wait_info()
    started_at = self.started_at if self.started_at is not None else time.time()
    lifetime = (
      self.lifetime if self.lifetime is not None else self.backend._get_effective_lifetime()
    )
    await self.backend.wait_for_completion_by_time(
      request_id=self.request_id,
      started_at=started_at,
      estimated_remaining_time=self.estimated_remaining_time,
      lifetime=lifetime,
      poll_interval=poll_interval,
      terminal_state="idle",
      progress_log_interval=self.backend.progress_log_interval,
      progress_callback=self.backend.progress_callback,
    )

  async def get_data_events(self) -> List[Dict[str, Any]]:
    events_dict = await self.backend.get_data_events(self.request_id)
    return events_dict.get(self.request_id, [])

  async def is_running(self) -> bool:
    """True if device is busy (only meaningful when command == 'ExecuteMethod')."""
    if self.command != "ExecuteMethod":
      return not self._future.done()
    return await self.backend.is_method_running()

  async def stop(self) -> None:
    """Stop the running method (no-op unless command == 'ExecuteMethod')."""
    if self.command == "ExecuteMethod":
      await self.backend.stop_method()


class ODTCBackend(ThermocyclerBackend):
  """ODTC backend using ODTC-specific SiLA interface.

  Implements ThermocyclerBackend interface for Inheco ODTC devices.
  Uses ODTCSiLAInterface for low-level SiLA communication with parallelism,
  state management, and lockId validation.

  ODTC dimensions for Thermocycler: size_x=156.5, size_y=248, size_z=124.3 (mm).
  Construct: backend = ODTCBackend(odtc_ip="...", variant=384); then
  Thermocycler(name="odtc1", size_x=156.5, size_y=248, size_z=124.3, backend=backend, ...).
  """

  def __init__(
    self,
    odtc_ip: str,
    variant: int = 96,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    lifetime_of_execution: Optional[float] = None,
    progress_log_interval: Optional[float] = 150.0,
    progress_callback: Optional[Callable[..., None]] = None,
    first_event_timeout_seconds: float = DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS,
  ):
    """Initialize ODTC backend.

    Args:
      odtc_ip: IP address of the ODTC device.
      variant: Well count (96 or 384). Device codes (960000, 384000, 3840000)
        are also accepted and normalized to 96/384.
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
      lifetime_of_execution: Max seconds to wait for async command completion (SiLA2 deadline). If None, uses 3 hours. Protocol execution is always bounded.
      progress_log_interval: Seconds between progress log lines during wait. None or 0 to disable. Default 150.0 (2.5 min); suitable for protocols from minutes to 1–2+ hours.
      progress_callback: Optional callback(ODTCProgress) called each progress_log_interval during wait.
      first_event_timeout_seconds: Timeout for waiting for first DataEvent (ExecuteMethod) and default lifetime/eta for status-driven commands (e.g. OpenDoor). Default 60 s.
    """
    super().__init__()
    self._variant = normalize_variant(variant)
    self._current_execution: Optional[ODTCExecution] = None
    self._simulation_mode: bool = False
    self._protocol_by_request_id: Dict[int, Union[Protocol, ODTCProtocol]] = {}
    self.progress_log_interval: Optional[float] = progress_log_interval
    self.progress_callback: Optional[Callable[..., None]] = progress_callback
    self._sila = ODTCSiLAInterface(
      machine_ip=odtc_ip,
      client_ip=client_ip,
      logger=logger,
      lifetime_of_execution=lifetime_of_execution,
    )
    self._first_event_timeout_seconds = first_event_timeout_seconds
    self.logger = logger or logging.getLogger(__name__)

  @property
  def odtc_ip(self) -> str:
    """IP address of the ODTC device."""
    return self._sila._machine_ip

  @property
  def variant(self) -> int:
    """ODTC variant (96 or 384)."""
    return self._variant

  @property
  def current_execution(self) -> Optional[ODTCExecution]:
    """Current method execution handle (set when a method is started with wait=False or wait=True)."""
    return self._current_execution

  @property
  def simulation_mode(self) -> bool:
    """Whether the device is in simulation mode (from the last reset() call).

    Reflects the last simulation_mode passed to reset(); valid once that Reset
    has completed (or immediately if wait=True). Use this to check state without
    calling reset again.
    """
    return self._simulation_mode

  def _clear_current_execution_if(self, handle: ODTCExecution) -> None:
    """Clear _current_execution only if it still refers to the given handle."""
    if self._current_execution is handle:
      self._current_execution = None

  def _clear_execution_state_for_handle(self, handle: ODTCExecution) -> None:
    """Clear current execution and protocol cache for this handle."""
    self._clear_current_execution_if(handle)
    self._protocol_by_request_id.pop(handle.request_id, None)

  async def setup(
    self,
    full: bool = True,
    simulation_mode: bool = False,
    max_attempts: int = 3,
    retry_backoff_base_seconds: float = 1.0,
  ) -> None:
    """Prepare the ODTC connection.

    When full=True (default): full SiLA lifecycle (event receiver, Reset,
    Initialize, verify idle), with optional retry and exponential backoff.
    When full=False: only start the event receiver (reconnect without reset);
    use after session loss so a running method is not aborted; then use
    wait_for_completion_by_time() or a persisted handle's wait_resumable().

    Args:
      full: If True, run full lifecycle (event receiver + Reset + Initialize).
        If False, only start event receiver; do not call Reset or Initialize.
      simulation_mode: Used only when full=True; passed to reset(). When True,
        device runs in SiLA simulation mode (commands return immediately with
        estimated duration; valid until next Reset).
      max_attempts: When full=True, number of attempts for the full path
        (default 3). On failure, retry with exponential backoff.
      retry_backoff_base_seconds: Base delay in seconds for backoff; delay
        before attempt i (i > 0) is retry_backoff_base_seconds * (2 ** (i - 1)).
    """
    if not full:
      await self._sila.setup()
      return

    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
      try:
        await self._setup_full_path(simulation_mode)
        return
      except Exception as e:  # noqa: BLE001
        last_error = e
        if attempt < max_attempts - 1:
          wait_time = retry_backoff_base_seconds * (2**attempt)
          self.logger.warning(
            "Setup attempt %s/%s failed: %s. Retrying in %.1fs.",
            attempt + 1,
            max_attempts,
            e,
            wait_time,
          )
          await asyncio.sleep(wait_time)
        else:
          raise last_error from e
    if last_error is not None:
      raise last_error from last_error

  async def _setup_full_path(self, simulation_mode: bool) -> None:
    """Run the full connection path: event receiver, Reset, Initialize, verify idle."""
    await self._sila.setup()

    # Wait for device to leave startup (boot takes a few seconds after power cycle)
    for _ in range(30):
      status = await self.get_status()
      if status != SiLAState.STARTUP.value:
        break
      await asyncio.sleep(1)

    await self.reset(simulation_mode=simulation_mode)

    status = await self.get_status()
    self.logger.info(f"GetStatus returned raw state: {status!r} (type: {type(status).__name__})")

    if status == SiLAState.STANDBY.value:
      self.logger.info("Device is in standby state, calling Initialize...")
      await self.initialize()

      status_after_init = await self.get_status()

      if status_after_init == SiLAState.IDLE.value:
        self.logger.info("Device successfully initialized and is in idle state")
      else:
        raise RuntimeError(
          f"Device is not in idle state after Initialize. Expected {SiLAState.IDLE.value!r}, "
          f"but got {status_after_init!r}."
        )
    elif status == SiLAState.IDLE.value:
      self.logger.info("Device already in idle state after Reset")
    else:
      raise RuntimeError(
        f"Unexpected device state after Reset: {status!r}. Expected {SiLAState.STANDBY.value!r} or {SiLAState.IDLE.value!r}."
      )

  async def stop(self) -> None:
    """Close the ODTC device connection."""
    await self._sila.close()

  def serialize(self) -> dict:
    """Return serialized representation of the backend.

    Only includes "port" when the SiLA event receiver has been started (e.g. after
    setup()), so the visualizer can serialize the deck without connecting to the ODTC.
    """
    out = {
      **super().serialize(),
      "odtc_ip": self.odtc_ip,
      "variant": self.variant,
    }
    try:
      out["port"] = self._sila.bound_port
    except RuntimeError:
      # Server not started yet; omit port so deck can be serialized without connecting
      pass
    return out

  def _get_effective_lifetime(self) -> float:
    """Effective max wait for async command completion (seconds)."""
    if self._sila._lifetime_of_execution is not None:
      return self._sila._lifetime_of_execution
    return DEFAULT_LIFETIME_OF_EXECUTION

  async def _run_async_command(
    self,
    command: ODTCCommand,
    wait: bool,
    method_name: Optional[str] = None,
    estimated_duration_s: Optional[float] = None,
    **send_kwargs: Any,
  ) -> Optional[ODTCExecution]:
    """Run an async SiLA command; return None if wait else execution handle."""
    if wait:
      await self._sila.send_command(command, **send_kwargs)
      return None
    fut, request_id, started_at = await self._sila.start_command(command, **send_kwargs)
    effective = self._get_effective_lifetime()
    if command == ODTCCommand.EXECUTE_METHOD:
      first_payload = await self._sila.wait_for_first_data_event(
        request_id, self._first_event_timeout_seconds
      )
      progress = ODTCProgress.from_data_event(first_payload, None)
      if estimated_duration_s is not None and estimated_duration_s > 0:
        eta = max(0.0, estimated_duration_s - progress.elapsed_s)
        lifetime = min(eta + LIFETIME_BUFFER_SECONDS, effective)
      else:
        eta = effective
        lifetime = effective
      return ODTCExecution(
        request_id=request_id,
        command=command,
        _future=fut,
        backend=self,
        estimated_remaining_time=eta,
        started_at=started_at,
        lifetime=lifetime,
        method_name=method_name or "",
      )

    eta = self._first_event_timeout_seconds
    lifetime = min(
      self._first_event_timeout_seconds + LIFETIME_BUFFER_SECONDS,
      effective,
    )
    return ODTCExecution(
      request_id=request_id,
      command=command,
      _future=fut,
      backend=self,
      estimated_remaining_time=eta,
      started_at=started_at,
      lifetime=lifetime,
    )

  async def _execute_method_impl(
    self,
    method_name: str,
    wait: bool,
    priority: Optional[int] = None,
    protocol: Optional[Protocol] = None,
  ) -> ODTCExecution:
    """Internal: run ExecuteMethod, register protocol, return handle."""
    self._current_execution = None
    params: Dict[str, Any] = {"methodName": method_name}
    if priority is not None:
      params["priority"] = priority

    protocol_to_register: Optional[Union[Protocol, ODTCProtocol]] = None
    _, premethods = await self.list_methods()
    if method_name in premethods:
      estimated_duration_s = PREMETHOD_ESTIMATED_DURATION_SECONDS
      method_set = await self.get_method_set()
      resolved = get_method_by_name(method_set, method_name)
      if resolved is not None:
        protocol_to_register = resolved
    elif protocol is not None:
      config = self.get_default_config()
      odtc_protocol = protocol_to_odtc_protocol(protocol, config=config)
      estimated_duration_s = estimate_method_duration_seconds(odtc_protocol)
      protocol_to_register = protocol
    else:
      fetched = await self.get_protocol(method_name)
      if fetched is not None:
        estimated_duration_s = estimate_method_duration_seconds(fetched)
        protocol_to_register = fetched
      else:
        estimated_duration_s = self._get_effective_lifetime()

    handle = await self._run_async_command(
      ODTCCommand.EXECUTE_METHOD,
      False,
      method_name=method_name,
      estimated_duration_s=estimated_duration_s,
      **params,
    )
    assert handle is not None
    handle._future.add_done_callback(lambda _: self._clear_execution_state_for_handle(handle))
    if protocol_to_register is not None:
      self._protocol_by_request_id[handle.request_id] = protocol_to_register
    self._current_execution = handle
    if wait:
      await handle.wait()
    return handle

  # ============================================================================
  # Request + normalized response
  # ============================================================================

  async def _request(self, command: str, **kwargs: Any) -> _NormalizedSiLAResponse:
    """Send command and return normalized response (dict or ET wrapped)."""
    raw = await self._sila.send_command(command, **kwargs)
    return _NormalizedSiLAResponse.from_raw(raw, command)

  async def _get_method_set_xml(self) -> str:
    """Get MethodsXML parameter string from GetParameters response."""
    resp = await self._request("GetParameters")
    return resp.get_parameter_string("MethodsXML")

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
    resp = await self._request("GetStatus")
    state = resp.get_value("GetStatusResponse", "state")
    return str(state)

  async def initialize(self, wait: bool = True) -> Optional[ODTCExecution]:
    """Initialize the device (SiLA: standby -> idle)."""
    return await self._run_async_command(ODTCCommand.INITIALIZE, wait)

  async def reset(
    self,
    simulation_mode: bool = False,
    wait: bool = True,
  ) -> Optional[ODTCExecution]:
    """Reset the device (SiLA: startup -> standby, register event receiver)."""
    self._simulation_mode = simulation_mode
    return await self._run_async_command(
      ODTCCommand.RESET,
      wait,
      deviceId="ODTC",
      eventReceiverURI=self._sila.event_receiver_uri,
      simulationMode=simulation_mode,
    )

  async def get_device_identification(self) -> dict:
    """Get device identification information.

    Returns:
      Device identification dictionary.
    """
    resp = await self._request("GetDeviceIdentification")
    result = resp.get_value(
      "GetDeviceIdentificationResponse",
      "GetDeviceIdentificationResult",
      required=False,
    )
    return result if isinstance(result, dict) else {}

  async def lock_device(
    self, lock_id: str, lock_timeout: Optional[float] = None, wait: bool = True
  ) -> Optional[ODTCExecution]:
    """Lock the device for exclusive access (SiLA: LockDevice)."""
    params: Dict[str, Any] = {"lockId": lock_id, "PMSId": "PyLabRobot"}
    if lock_timeout is not None:
      params["lockTimeout"] = lock_timeout
    return await self._run_async_command(ODTCCommand.LOCK_DEVICE, wait, **params)

  async def unlock_device(self, wait: bool = True) -> Optional[ODTCExecution]:
    """Unlock the device (SiLA: UnlockDevice)."""
    if self._sila._lock_id is None:
      raise RuntimeError("Device is not locked")
    return await self._run_async_command(ODTCCommand.UNLOCK_DEVICE, wait)

  async def open_door(self, wait: bool = True) -> Optional[ODTCExecution]:
    """Open the door (thermocycler lid)."""
    return await self._run_async_command(ODTCCommand.OPEN_DOOR, wait)

  async def close_door(self, wait: bool = True) -> Optional[ODTCExecution]:
    """Close the door (thermocycler lid)."""
    return await self._run_async_command(ODTCCommand.CLOSE_DOOR, wait)

  async def read_temperatures(self) -> ODTCSensorValues:
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    resp = await self._request("ReadActualTemperature")
    sensor_xml = resp.get_parameter_string("SensorValues", allow_root_fallback=True)
    sensor_values = parse_sensor_values(sensor_xml)
    self.logger.debug("ReadActualTemperature: %s", sensor_values.format_compact())
    return sensor_values

  async def get_last_data(self) -> str:
    """Get temperature trace of last executed method (CSV format).

    Returns:
      CSV string with temperature trace data.
    """
    resp = await self._request("GetLastData")
    return str(resp.raw())

  # Method control commands (SiLA: ExecuteMethod; method = runnable protocol)
  async def execute_method(
    self,
    method_name: str,
    priority: Optional[int] = None,
    wait: bool = False,
    protocol: Optional[Protocol] = None,
  ) -> ODTCExecution:
    """Execute a method or premethod by name (SiLA: ExecuteMethod)."""
    return await self._execute_method_impl(method_name, wait, priority=priority, protocol=protocol)

  async def stop_method(self, wait: bool = True) -> Optional[ODTCExecution]:
    """Stop the currently running method (SiLA: StopMethod)."""
    return await self._run_async_command(ODTCCommand.STOP_METHOD, wait)

  # --- Method running and completion ---

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
          raise TimeoutError(f"Method execution did not complete within {timeout}s")
      await asyncio.sleep(poll_interval)

  async def wait_for_completion_by_time(
    self,
    request_id: int,
    started_at: float,
    estimated_remaining_time: Optional[float],
    lifetime: float,
    poll_interval: float = 5.0,
    terminal_state: str = "idle",
    progress_log_interval: Optional[float] = None,
    progress_callback: Optional[Callable[..., None]] = None,
  ) -> None:
    """Wait for async command completion using only wall-clock and GetStatus (resumable).

    Does not require the in-memory Future. Use after restart: persist request_id,
    started_at, estimated_remaining_time, lifetime from the handle, then call this
    with a reconnected backend.

    (a) Waits until time.time() >= started_at + estimated_remaining_time + buffer.
    (b) Then polls GetStatus every poll_interval until state == terminal_state or
        time.time() - started_at >= lifetime (then raises TimeoutError).
    When progress_log_interval is set, logs and/or calls progress_callback at that interval.

    Args:
      request_id: SiLA request ID (for logging and DataEvent lookup).
      started_at: time.time() when the command was sent.
      estimated_remaining_time: Device-estimated duration in seconds (or None).
      lifetime: Max seconds to wait (e.g. from handle.lifetime).
      poll_interval: Seconds between GetStatus calls.
      terminal_state: Device state that indicates command finished (default "idle").
      progress_log_interval: Seconds between progress reports; None/0 to disable. Uses backend default if None.
      progress_callback: Called with ODTCProgress each interval; uses backend default if None.

    Raises:
      TimeoutError: If lifetime exceeded before terminal state.
    """
    import time

    interval = (
      progress_log_interval if progress_log_interval is not None else self.progress_log_interval
    )
    callback = progress_callback if progress_callback is not None else self.progress_callback
    buffer = POLLING_START_BUFFER
    eta = estimated_remaining_time or 0.0
    while True:
      now = time.time()
      elapsed = now - started_at
      if elapsed >= lifetime:
        raise TimeoutError(f"Command (request_id={request_id}) did not complete within {lifetime}s")
      # Don't start polling until estimated time + buffer has passed
      remaining_wait = started_at + eta + buffer - now
      if remaining_wait > 0:
        await asyncio.sleep(min(remaining_wait, poll_interval))
        continue

      # From here: poll until terminal_state or timeout; progress via same loop as wait()
      async def _is_done() -> bool:
        status = await self.get_status()
        return status == terminal_state or (time.time() - started_at) >= lifetime

      progress_task: Optional[asyncio.Task[None]] = None
      if interval and interval > 0:
        progress_task = asyncio.create_task(
          self._run_progress_loop_until(request_id, interval, _is_done, callback)
        )
      try:
        while True:
          status = await self.get_status()
          if status == terminal_state:
            return
          if (time.time() - started_at) >= lifetime:
            raise TimeoutError(
              f"Command (request_id={request_id}) did not complete within {lifetime}s"
            )
          await asyncio.sleep(poll_interval)
      finally:
        if progress_task is not None:
          progress_task.cancel()
          try:
            await progress_task
          except asyncio.CancelledError:
            pass

  async def get_data_events(
    self, request_id: Optional[int] = None
  ) -> Dict[int, List[Dict[str, Any]]]:
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
    method_set_xml = await self._get_method_set_xml()
    return parse_method_set(method_set_xml)

  async def get_protocol(self, name: str) -> Optional[ODTCProtocol]:
    """Get a stored protocol by name (runnable methods only; premethods return None).

    Returns ODTCProtocol if a runnable method exists. Nested-loop validation
    runs only when converting to Protocol view (e.g. odtc_protocol_to_protocol).

    Args:
      name: Protocol name to retrieve.

    Returns:
      ODTCProtocol if a runnable method exists, None otherwise.
    """
    method_set = await self.get_method_set()
    resolved = get_method_by_name(method_set, name)
    if resolved is None or resolved.kind == "premethod":
      return None
    return resolved

  async def list_protocols(self) -> ProtocolList:
    """List all protocol names (methods and premethods) on the device.

    Returns:
      ProtocolList with .methods, .premethods, .all (flat list), and a __str__
      that prints Methods and PreMethods in clear sections. Iteration yields
      all names (methods then premethods).
    """
    method_set = await self.get_method_set()
    return ProtocolList(
      methods=[m.name for m in method_set.methods],
      premethods=[p.name for p in method_set.premethods],
    )

  async def list_methods(self) -> Tuple[List[str], List[str]]:
    """List method names and premethod names separately.

    Returns:
      Tuple of (method_names, premethod_names). Methods are runnable protocols;
      premethods are setup-only (e.g. set block/lid temperature).
    """
    method_set = await self.get_method_set()
    return ([m.name for m in method_set.methods], [p.name for p in method_set.premethods])

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

  # --- Protocol upload and run ---

  async def _upload_odtc_protocol(
    self,
    odtc: ODTCProtocol,
    allow_overwrite: bool = False,
    execute: bool = False,
    wait: bool = True,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> Optional[ODTCExecution]:
    """Upload a single ODTCProtocol (method or premethod) and optionally execute.

    Internal single entrypoint: builds one-item ODTCMethodSet and calls
    upload_method_set, then execute_method if execute=True.

    Returns:
      ODTCExecution if execute=True and wait=False; None otherwise.
    """
    resolved_name = odtc.name
    is_scratch = not odtc.name or odtc.name == ""
    resolved_datetime = odtc.datetime or generate_odtc_timestamp()

    if is_scratch and allow_overwrite is False:
      allow_overwrite = True
      if not odtc.name:
        self.logger.warning(
          "ODTCProtocol name resolved to scratch name '%s'. Auto-enabling allow_overwrite=True.",
          resolved_name,
        )

    odtc_copy = replace(odtc, name=resolved_name, datetime=resolved_datetime)
    if odtc.kind == "method":
      method_set = ODTCMethodSet(methods=[odtc_copy], premethods=[])
    else:
      method_set = ODTCMethodSet(methods=[], premethods=[odtc_copy])

    await self.upload_method_set(
      method_set,
      allow_overwrite=allow_overwrite,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

    if execute:
      handle = await self.execute_method(odtc.name, wait=wait)
      protocol_view = odtc_protocol_to_protocol(odtc_copy)
      self._protocol_by_request_id[handle.request_id] = protocol_view
      return handle
    return None

  async def upload_protocol(
    self,
    protocol: Union[Protocol, ODTCProtocol],
    name: Optional[str] = None,
    config: Optional[ODTCConfig] = None,
    block_max_volume: Optional[float] = None,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> str:
    """Upload a Protocol or ODTCProtocol to the device.

    Args:
      protocol: PyLabRobot Protocol or ODTCProtocol to upload.
      name: Method name. If None, uses scratch name "plr_currentProtocol".
      config: Optional ODTCConfig (used only when protocol is Protocol). If None,
        uses variant-aware defaults; if block_max_volume is provided and in 0–100 µL,
        sets fluid_quantity from it.
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
    if isinstance(protocol, ODTCProtocol):
      odtc_protocol = (
        replace(protocol, name=name or protocol.name) if name is not None else protocol
      )
    else:
      if config is None:
        if block_max_volume is not None and block_max_volume > 0 and block_max_volume <= 100:
          fluid_quantity = volume_to_fluid_quantity(block_max_volume)
          config = self.get_default_config(fluid_quantity=fluid_quantity)
        else:
          config = self.get_default_config()
      elif block_max_volume is not None and block_max_volume > 0:
        validate_volume_fluid_quantity(
          block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
        )
      if name is not None:
        config = replace(config, name=name)
      odtc_protocol = protocol_to_odtc_protocol(protocol, config=config)

    await self._upload_odtc_protocol(
      odtc_protocol,
      allow_overwrite=allow_overwrite,
      execute=False,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )
    return odtc_protocol.name

  async def run_stored_protocol(self, name: str, wait: bool = False, **kwargs) -> ODTCExecution:
    """Execute a stored protocol by name (single SiLA ExecuteMethod call).

    No fetch or round-trip; calls the instrument execute-by-name directly.
    Handle lifetime/ETA are event-driven (first DataEvent).

    Args:
      name: Name of the stored protocol (method) to run.
      wait: If False (default), start and return handle. If True, block until
          completion then return the (completed) handle.

    Returns:
      Execution handle (completed if wait=True).
    """
    method_set = await self.get_method_set()
    resolved = get_method_by_name(method_set, name)
    protocol_view = odtc_protocol_to_protocol(resolved) if resolved else None
    return await self.execute_method(name, wait=wait, protocol=protocol_view)

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

      def _existing_item_type(existing: ODTCProtocol) -> str:
        return "PreMethod" if existing.kind == "premethod" else "Method"

      # Check all method names (unified search)
      for method in method_set.methods:
        existing_method = get_method_by_name(existing_method_set, method.name)
        if existing_method is not None:
          conflicts.append(
            f"Method '{method.name}' already exists as {_existing_item_type(existing_method)}"
          )

      # Check all premethod names (unified search)
      for premethod in method_set.premethods:
        existing_method = get_method_by_name(existing_method_set, premethod.name)
        if existing_method is not None:
          conflicts.append(
            f"Method '{premethod.name}' already exists as {_existing_item_type(existing_method)}"
          )

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
    method_set_xml = await self._get_method_set_xml()
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
      wait: If True, block until set. If False (default), return execution handle.
      debug_xml: If True, log generated XML at DEBUG.
      xml_output_path: Optional path to save MethodSet XML.

    Returns:
      If wait=True: None. If wait=False: execution handle.
    """
    if not temperature:
      raise ValueError("At least one block temperature required")
    block_temp = temperature[0]
    if lid_temperature is not None:
      target_lid_temp = lid_temperature
    else:
      constraints = self.get_constraints()
      target_lid_temp = constraints.max_lid_temp

    protocol = ODTCProtocol(
      stages=[],
      kind="premethod",
      target_block_temperature=block_temp,
      target_lid_temperature=target_lid_temp,
      datetime=generate_odtc_timestamp(),
    )
    await self._upload_odtc_protocol(
      protocol,
      allow_overwrite=True,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )
    handle = await self.execute_method(protocol.name, wait=wait)
    # Register ODTCProtocol so progress shows correct target_block_temperature
    # (device DataEvent often reports current setpoint during premethod ramp, not final target).
    self._protocol_by_request_id[handle.request_id] = protocol
    return handle

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
    protocol: Union[Protocol, ODTCProtocol],
    block_max_volume: float,
    **kwargs: Any,
  ) -> ODTCExecution:
    """Execute thermocycler protocol (convert if needed, upload, execute).

    Accepts Protocol or ODTCProtocol. Converts Protocol to ODTCProtocol when
    needed, uploads, then executes by name. Always returns immediately with a
    Execution handle; to block until completion, await handle.wait() or
    use wait_for_profile_completion(). Config is derived from block_max_volume
    and backend variant when protocol is Protocol and config is not provided.

    Args:
      protocol: Protocol or ODTCProtocol to execute.
      block_max_volume: Maximum block volume (µL) for safety; used to set
        fluid_quantity when protocol is Protocol and config is None.
      **kwargs: Backend-specific options. ODTC accepts ``config`` (ODTCConfig,
        optional); used only when protocol is Protocol.

    Returns:
      Execution handle. Caller can await handle.wait() or
      wait_for_profile_completion() to block until done.
    """
    if isinstance(protocol, ODTCProtocol):
      odtc_protocol = protocol
      if odtc_protocol.kind != "method":
        raise ValueError("run_protocol requires a method (ODTCProtocol with kind='method')")
    else:
      config = kwargs.pop("config", None)
      if config is None:
        if block_max_volume > 0 and block_max_volume <= 100:
          fluid_quantity = volume_to_fluid_quantity(block_max_volume)
          config = self.get_default_config(fluid_quantity=fluid_quantity)
        else:
          config = self.get_default_config()
        if block_max_volume > 0 and block_max_volume <= 100:
          validate_volume_fluid_quantity(
            block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
          )
      else:
        if block_max_volume > 0:
          validate_volume_fluid_quantity(
            block_max_volume, config.fluid_quantity, is_premethod=False, logger=self.logger
          )
      odtc_protocol = protocol_to_odtc_protocol(protocol, config=config)

    await self._upload_odtc_protocol(odtc_protocol, allow_overwrite=True, execute=False)
    resolved_name = odtc_protocol.name
    handle = await self.execute_method(resolved_name, wait=False)
    protocol_view = odtc_protocol_to_protocol(odtc_protocol)
    self._protocol_by_request_id[handle.request_id] = protocol_view
    return handle

  # --- Temperatures and lid/block status ---

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

  # --- Progress and step/cycle (DataEvent) ---

  async def _report_progress_once(
    self,
    request_id: int,
    callback: Optional[Callable[..., None]] = None,
  ) -> None:
    """Fetch latest DataEvent for request_id, update snapshot, and log or invoke progress_callback.

    Used by wait_for_completion_by_time and by ODTCExecution.wait() (background task).
    No-op if _get_progress returns None (e.g. non-method command).
    callback: Override for this call; if None, uses self.progress_callback.
    """
    progress = await self._get_progress(request_id)
    if progress is None:
      return
    cb = callback if callback is not None else self.progress_callback
    if cb is not None:
      try:
        cb(progress)
      except Exception:  # noqa: S110
        pass
    else:
      self.logger.info(progress.format_progress_log_message())

  async def _run_progress_loop_until(
    self,
    request_id: int,
    interval: float,
    done_async: Callable[[], Awaitable[bool]],
    callback: Optional[Callable[..., None]] = None,
  ) -> None:
    """Run progress reporting every interval until done_async() returns True.

    Single definition dispatched by both Future-based wait() and polling-based
    wait_for_completion_by_time. Stops when done_async() returns True (e.g.
    future.done() or status == terminal_state or timeout).
    """
    while not (await done_async()):
      await self._report_progress_once(request_id, callback=callback)
      await asyncio.sleep(interval)

  def _protocol_total_step_count(self, protocol: Protocol) -> int:
    """Total expanded step count from Protocol (for display when device does not send it)."""
    return sum(len(stage.steps) * stage.repeats for stage in protocol.stages)

  async def _get_progress(self, request_id: int) -> Optional[ODTCProgress]:
    """Get progress from latest DataEvent (elapsed, temps, step/cycle/hold). Returns None if no protocol registered."""
    stored = self._protocol_by_request_id.get(request_id)
    if stored is None:
      return None
    if isinstance(stored, ODTCProtocol):
      odtc_protocol = stored
    else:
      odtc_protocol = protocol_to_odtc_protocol(stored, self.get_default_config())
    events_dict = await self.get_data_events(request_id)
    events = events_dict.get(request_id, [])
    payload = events[-1] if events else None
    return ODTCProgress.from_data_event(payload, odtc_protocol=odtc_protocol)

  def _request_id_for_get_progress(self) -> Optional[int]:
    """Request ID of current execution for get_* methods; None if none or done."""
    ex = self._current_execution
    if ex is None or ex.done:
      return None
    return ex.request_id

  async def get_progress_snapshot(self) -> Optional[ODTCProgress]:
    """Get progress from the latest DataEvent for the current run (elapsed, temperatures, step/cycle/hold).

    Returns None if no protocol is running. Returns ODTCProgress: elapsed, temperatures,
    and step/cycle/hold derived from elapsed time and the run's protocol when registered.
    """
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      return None
    return await self._get_progress(request_id)

  async def get_hold_time(self) -> float:
    """Get remaining hold time in seconds for the current step."""
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      raise RuntimeError("No profile running; get_hold_time requires an active method execution.")
    progress = await self._get_progress(request_id)
    if progress is None:
      raise RuntimeError(
        "No protocol associated with this run; get_hold_time requires a registered protocol."
      )
    return progress.remaining_hold_s if progress.remaining_hold_s is not None else 0.0

  async def get_current_cycle_index(self) -> int:
    """Get zero-based current cycle index."""
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      raise RuntimeError(
        "No profile running; get_current_cycle_index requires an active method execution."
      )
    progress = await self._get_progress(request_id)
    if progress is None:
      raise RuntimeError(
        "No protocol associated with this run; get_current_cycle_index requires a registered protocol."
      )
    return progress.current_cycle_index if progress.current_cycle_index is not None else 0

  async def get_total_cycle_count(self) -> int:
    """Get total cycle count for the current stage."""
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      raise RuntimeError(
        "No profile running; get_total_cycle_count requires an active method execution."
      )
    progress = await self._get_progress(request_id)
    if progress is None:
      raise NotImplementedError(
        "ODTC does not report total cycle count; no protocol associated with this run."
      )
    return progress.total_cycle_count if progress.total_cycle_count is not None else 0

  async def get_current_step_index(self) -> int:
    """Get zero-based current step index within the cycle."""
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      raise RuntimeError(
        "No profile running; get_current_step_index requires an active method execution."
      )
    progress = await self._get_progress(request_id)
    if progress is None:
      raise RuntimeError(
        "No protocol associated with this run; get_current_step_index requires a registered protocol."
      )
    return progress.current_step_index if progress.current_step_index is not None else 0

  async def get_total_step_count(self) -> int:
    """Get total number of steps in the current cycle."""
    request_id = self._request_id_for_get_progress()
    if request_id is None:
      raise RuntimeError(
        "No profile running; get_total_step_count requires an active method execution."
      )
    progress = await self._get_progress(request_id)
    if progress is None:
      raise NotImplementedError(
        "ODTC does not report total step count; no protocol associated with this run."
      )
    return progress.total_step_count if progress.total_step_count is not None else 0
