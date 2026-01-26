"""ODTC backend implementing ThermocyclerBackend interface using ODTC SiLA interface."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pylabrobot.machines.backend import MachineBackend
from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol

from .odtc_sila_interface import ODTCSiLAInterface
from .odtc_xml import (
  ODTCMethod,
  ODTCMethodSet,
  ODTCSensorValues,
  get_method_by_name,
  method_set_to_xml,
  parse_method_set,
  parse_method_set_file,
  parse_sensor_values,
)


@dataclass
class MethodExecution:
  """Handle for an executing method that can be awaited or checked.

  This handle is returned from execute_method(wait=False) and provides:
  - Awaitable interface (can be awaited like a Task)
  - Request ID access for DataEvent tracking
  - Status checking methods
  """

  request_id: int
  method_name: str
  _future: asyncio.Future[Any]
  backend: "ODTCBackend"

  def __await__(self):
    """Make this awaitable like a Task."""
    return self._future.__await__()

  async def wait(self) -> None:
    """Wait for method completion."""
    await self._future

  async def is_running(self) -> bool:
    """Check if method is still running."""
    return await self.backend.is_method_running()


class ODTCBackend(ThermocyclerBackend):
  """ODTC backend using ODTC-specific SiLA interface.

  Implements ThermocyclerBackend interface for Inheco ODTC devices.
  Uses ODTCSiLAInterface for low-level SiLA communication with parallelism,
  state management, and lockId validation.
  """

  def __init__(
    self,
    odtc_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ):
    """Initialize ODTC backend.

    Args:
      odtc_ip: IP address of the ODTC device.
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
    """
    super().__init__()
    self._sila = ODTCSiLAInterface(machine_ip=odtc_ip, client_ip=client_ip, logger=logger)
    self.logger = logger or logging.getLogger(__name__)

  async def setup(self) -> None:
    """Initialize the ODTC device connection.
    
    Performs the full SiLA connection lifecycle:
    1. Sets up the HTTP event receiver server
    2. Calls Reset to move from Startup -> Standby and register event receiver
    3. Waits for Reset to complete and checks state
    4. Calls Initialize to move from Standby -> Idle
    """
    # Step 1: Set up the HTTP event receiver server
    await self._sila.setup()
    
    # Step 2: Reset (Startup -> Standby) - registers event receiver URI
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
    
    # Normalize state string for comparison (device returns lowercase)
    status_normalized = status.lower() if status else status
    
    # Step 4: Initialize (Standby -> Idle) if we're in Standby
    if status_normalized == "standby":
      await self.initialize()
    elif status_normalized == "idle":
      # Already in Idle, nothing to do
      self.logger.info("Device already in Idle state after Reset")
    else:
      raise RuntimeError(
        f"Unexpected device state after Reset: {status}. Expected standby or idle."
      )

  async def stop(self) -> None:
    """Close the ODTC device connection."""
    await self._sila.close()

  def serialize(self) -> dict:
    """Return serialized representation of the backend."""
    return {
      **super().serialize(),
      "odtc_ip": self._sila._machine_ip,
      "port": self._sila.bound_port,
    }

  # ============================================================================
  # Basic ODTC Commands (from plan)
  # ============================================================================

  async def get_status(self) -> str:
    """Get device status state.

    Returns:
      Device state string (e.g., "Idle", "Busy", "Standby").
    """
    resp = await self._sila.send_command("GetStatus")
    # GetStatus is synchronous - resp is a dict from soap_decode
    if isinstance(resp, dict):
      # Try different possible response structures
      # Structure 1: GetStatusResponse -> state (like SCILABackend)
      state = resp.get("GetStatusResponse", {}).get("state")
      if state:
        return state  # type: ignore
      # Structure 2: GetStatusResponse -> GetStatusResult -> state
      state = resp.get("GetStatusResponse", {}).get("GetStatusResult", {}).get("state")
      if state:
        return state  # type: ignore
      # Structure 3: Direct state key
      state = resp.get("state")
      if state:
        return state  # type: ignore
      # Debug: log the actual response structure to help diagnose
      self.logger.debug(f"GetStatus response keys: {list(resp.keys())}")
      if "GetStatusResponse" in resp:
        self.logger.debug(f"GetStatusResponse keys: {list(resp['GetStatusResponse'].keys())}")
      return "Unknown"
    else:
      # Fallback if response format is different
      self.logger.warning(f"GetStatus returned non-dict response: {type(resp)}")
      return "Unknown"

  async def initialize(self) -> None:
    """Initialize the device (must be in Standby state)."""
    await self._sila.send_command("Initialize")

  async def reset(
    self,
    device_id: str = "ODTC",
    event_receiver_uri: Optional[str] = None,
    simulation_mode: bool = False,
  ) -> None:
    """Reset the device.

    Args:
      device_id: Device identifier.
      event_receiver_uri: Event receiver URI (auto-detected if None).
      simulation_mode: Enable simulation mode.
    """
    if event_receiver_uri is None:
      event_receiver_uri = f"http://{self._sila._client_ip}:{self._sila.bound_port}/"
    await self._sila.send_command(
      "Reset",
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
    if isinstance(resp, dict):
      return resp.get("GetDeviceIdentificationResponse", {}).get("GetDeviceIdentificationResult", {})  # type: ignore
    else:
      return {}

  async def lock_device(self, lock_id: str, lock_timeout: Optional[float] = None) -> None:
    """Lock the device for exclusive access.

    Args:
      lock_id: Unique lock identifier.
      lock_timeout: Lock timeout in seconds (optional).
    """
    params: dict = {"lockId": lock_id, "PMSId": "PyLabRobot"}
    if lock_timeout is not None:
      params["lockTimeout"] = lock_timeout
    await self._sila.send_command("LockDevice", lock_id=lock_id, **params)

  async def unlock_device(self) -> None:
    """Unlock the device."""
    # Must provide the lockId that was used to lock it
    if self._sila._lock_id is None:
      raise RuntimeError("Device is not locked")
    await self._sila.send_command("UnlockDevice", lock_id=self._sila._lock_id)

  # Door control commands
  async def open_door(self) -> None:
    """Open the drawer door (equivalent to PrepareForOutput)."""
    await self._sila.send_command("OpenDoor")

  async def close_door(self) -> None:
    """Close the drawer door (equivalent to PrepareForInput)."""
    await self._sila.send_command("CloseDoor")

  async def prepare_for_output(self, position: Optional[int] = None) -> None:
    """Prepare for output (equivalent to OpenDoor)."""
    params = {}
    if position is not None:
      params["position"] = position
    await self._sila.send_command("PrepareForOutput", **params)

  async def prepare_for_input(self, position: Optional[int] = None) -> None:
    """Prepare for input (equivalent to CloseDoor)."""
    params = {}
    if position is not None:
      params["position"] = position
    await self._sila.send_command("PrepareForInput", **params)

  # Sensor commands
  async def read_temperatures(self) -> ODTCSensorValues:
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    resp = await self._sila.send_command("ReadActualTemperature")
    # Response is ElementTree root - find SensorValues parameter
    if resp is None:
      raise ValueError("Empty response from ReadActualTemperature")

    # Response structure: ResponseData/Parameter[@name='SensorValues']/String
    param = resp.find(".//Parameter[@name='SensorValues']")
    if param is None:
      raise ValueError("SensorValues parameter not found in response")
    sensor_str_elem = param.find("String")
    if sensor_str_elem is None or sensor_str_elem.text is None:
      raise ValueError("SensorValues String element not found")
    # Parse the XML string (it's escaped in the response)
    sensor_xml = sensor_str_elem.text
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

  # Method control commands
  async def execute_method(
    self,
    method_name: str,
    priority: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[MethodExecution]:
    """Execute a method or premethod by name.

    Args:
      method_name: Name of the method or premethod to execute.
      priority: Priority (not used by ODTC, but part of SiLA spec).
      wait: If True, block until completion and return None.
          If False, return MethodExecution handle immediately.

    Returns:
      If wait=True: None (blocks until complete)
      If wait=False: MethodExecution handle (awaitable, has request_id)
    """
    params: dict = {"methodName": method_name}
    if priority is not None:
      params["priority"] = priority

    if wait:
      # Blocking: await send_command normally
      await self._sila.send_command("ExecuteMethod", return_request_id=False, **params)
      return None
    else:
      # Use send_command with return_request_id=True to get Future and request_id
      fut, request_id = await self._sila.send_command(
        "ExecuteMethod",
        return_request_id=True,
        **params
      )

      return MethodExecution(
        request_id=request_id,
        method_name=method_name,
        _future=fut,
        backend=self
      )

  async def stop_method(self) -> None:
    """Stop currently running method."""
    await self._sila.send_command("StopMethod")

  async def is_method_running(self) -> bool:
    """Check if a method is currently running.

    Uses GetStatus to check device state. Returns True if state is 'busy',
    indicating a method execution is in progress.

    Returns:
      True if method is running (state is 'busy'), False otherwise.
    """
    status = await self.get_status()
    return status.lower() == "busy"

  async def wait_for_method_completion(
    self,
    poll_interval: float = 5.0,
    timeout: Optional[float] = None,
  ) -> None:
    """Wait until method execution completes.

    Polls GetStatus at poll_interval until state returns to 'idle'.
    Useful when method was started with wait=False and you need to wait.

    Args:
      poll_interval: Seconds between status checks. Default 5.0.
      timeout: Maximum seconds to wait. None for no timeout.

    Raises:
      TimeoutError: If timeout is exceeded.
    """
    import time

    start_time = time.time()
    while await self.is_method_running():
      if timeout is not None:
        elapsed = time.time() - start_time
        if elapsed > timeout:
          raise TimeoutError(
            f"Method execution did not complete within {timeout}s"
          )
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
    if resp is None:
      raise ValueError("Empty response from GetParameters")

    # Extract MethodsXML parameter
    param = resp.find(".//Parameter[@name='MethodsXML']")
    if param is None:
      raise ValueError("MethodsXML parameter not found in response")

    string_elem = param.find("String")
    if string_elem is None or string_elem.text is None:
      raise ValueError("MethodsXML String element not found")

    # Parse MethodSet XML (it's escaped in the response)
    method_set_xml = string_elem.text
    return parse_method_set(method_set_xml)

  async def get_method_by_name(self, method_name: str) -> Optional[ODTCMethod]:
    """Get a specific method by name from the device.

    Args:
      method_name: Name of the method to retrieve.

    Returns:
      ODTCMethod if found, None otherwise.
    """
    method_set = await self.get_method_set()
    return get_method_by_name(method_set, method_name)

  async def upload_method_set(self, method_set: ODTCMethodSet) -> None:
    """Upload a MethodSet to the device.

    Args:
      method_set: ODTCMethodSet to upload.
    """
    method_set_xml = method_set_to_xml(method_set)

    # SetParameters expects paramsXML in ResponseType_1.2.xsd format
    # Format: <ParameterSet><Parameter parameterType="String" name="MethodsXML"><String>...</String></Parameter></ParameterSet>
    import xml.etree.ElementTree as ET
    param_set = ET.Element("ParameterSet")
    param = ET.SubElement(param_set, "Parameter", parameterType="String", name="MethodsXML")
    string_elem = ET.SubElement(param, "String")
    # XML needs to be escaped for embedding in another XML
    string_elem.text = method_set_xml

    params_xml = ET.tostring(param_set, encoding="unicode", xml_declaration=False)
    await self._sila.send_command("SetParameters", paramsXML=params_xml)

  async def upload_method_set_from_file(self, filepath: str) -> None:
    """Load and upload a MethodSet XML file to the device.

    Args:
      filepath: Path to MethodSet XML file.
    """
    method_set = parse_method_set_file(filepath)
    await self.upload_method_set(method_set)

  async def save_method_set_to_file(self, filepath: str) -> None:
    """Download methods from device and save to file.

    Args:
      filepath: Path to save MethodSet XML file.
    """
    resp = await self._sila.send_command("GetParameters")
    if resp is None:
      raise ValueError("Empty response from GetParameters")

    # Extract MethodsXML parameter
    param = resp.find(".//Parameter[@name='MethodsXML']")
    if param is None:
      raise ValueError("MethodsXML parameter not found in response")

    string_elem = param.find("String")
    if string_elem is None or string_elem.text is None:
      raise ValueError("MethodsXML String element not found")

    # XML is escaped in the response, so we get it as-is
    method_set_xml = string_elem.text
    # Write to file
    with open(filepath, "w", encoding="utf-8") as f:
      f.write(method_set_xml)

  # ============================================================================
  # ThermocyclerBackend Abstract Methods
  # ============================================================================

  async def open_lid(self) -> None:
    """Open thermocycler lid (maps to OpenDoor)."""
    await self.open_door()

  async def close_lid(self) -> None:
    """Close thermocycler lid (maps to CloseDoor)."""
    await self.close_door()

  async def set_block_temperature(self, temperature: List[float]) -> None:
    """Set block temperature.

    Note: ODTC doesn't have a direct SetBlockTemperature command.
    Temperature is controlled via ExecuteMethod with PreMethod or Method.
    This is a placeholder that raises NotImplementedError.

    Args:
      temperature: Target temperature(s) in °C.
    """
    raise NotImplementedError(
      "ODTC doesn't support direct block temperature setting. "
      "Use ExecuteMethod with a PreMethod or Method instead."
    )

  async def set_lid_temperature(self, temperature: List[float]) -> None:
    """Set lid temperature.

    Note: ODTC doesn't have a direct SetLidTemperature command.
    Lid temperature is controlled via ExecuteMethod with PreMethod or Method.
    This is a placeholder that raises NotImplementedError.

    Args:
      temperature: Target temperature(s) in °C.
    """
    raise NotImplementedError(
      "ODTC doesn't support direct lid temperature setting. "
      "Use ExecuteMethod with a PreMethod or Method instead."
    )

  async def deactivate_block(self) -> None:
    """Deactivate block (maps to StopMethod)."""
    await self.stop_method()

  async def deactivate_lid(self) -> None:
    """Deactivate lid (maps to StopMethod)."""
    await self.stop_method()

  async def run_protocol(self, protocol: Protocol, block_max_volume: float) -> None:
    """Execute thermocycler protocol.

    Note: This requires converting Protocol to ODTCMethod and uploading it.
    For now, this is a placeholder.

    Args:
      protocol: Protocol to execute.
      block_max_volume: Maximum block volume (µL).
    """
    raise NotImplementedError(
      "Protocol execution requires converting Protocol to ODTCMethod. "
      "Use protocol_to_odtc_method() from odtc_xml.py, then upload and execute."
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
    # ODTC doesn't expose target temperature directly
    # Would need to query current method execution state
    raise RuntimeError("Target temperature not available - method execution state not tracked")

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
    # ODTC doesn't expose target temperature directly
    raise RuntimeError("Target temperature not available - method execution state not tracked")

  async def get_lid_open(self) -> bool:
    """Check if lid is open.

    Returns:
      True if lid/door is open.
    """
    # Would need GetDoorStatus command - for now, return False
    # TODO: Implement GetDoorStatus if available
    return False

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

    Returns:
      Remaining hold time in seconds.
    """
    # Not directly available from ODTC - would need method execution state
    raise NotImplementedError("Hold time not available - method execution state not tracked")

  async def get_current_cycle_index(self) -> int:
    """Get current cycle index.

    Returns:
      Zero-based cycle index.
    """
    # Not directly available from ODTC - would need method execution state
    raise NotImplementedError("Cycle index not available - method execution state not tracked")

  async def get_total_cycle_count(self) -> int:
    """Get total cycle count.

    Returns:
      Total number of cycles.
    """
    # Not directly available from ODTC - would need method execution state
    raise NotImplementedError("Cycle count not available - method execution state not tracked")

  async def get_current_step_index(self) -> int:
    """Get current step index.

    Returns:
      Zero-based step index.
    """
    # Not directly available from ODTC - would need method execution state
    raise NotImplementedError("Step index not available - method execution state not tracked")

  async def get_total_step_count(self) -> int:
    """Get total step count.

    Returns:
      Total number of steps.
    """
    # Not directly available from ODTC - would need method execution state
    raise NotImplementedError("Step count not available - method execution state not tracked")
