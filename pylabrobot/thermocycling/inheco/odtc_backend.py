"""ODTC backend implementing ThermocyclerBackend interface using ODTC SiLA interface."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Union

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol

from .odtc_model import (
  ODTCPID,
  ODTCConfig,
  ODTCHardwareConstraints,
  ODTCMethodSet,
  ODTCProgress,
  ODTCProtocol,
  ODTCVariant,
  ODTCSensorValues,
  get_constraints,
  normalize_variant,
  volume_to_fluid_quantity,
)
from .odtc_protocol import (
  build_progress_from_data_event,
  protocol_to_odtc_protocol,
)
from .odtc_xml import (
  method_set_to_xml,
  parse_method_set,
  parse_method_set_file,
  parse_sensor_values,
)
from pylabrobot.storage.inheco.scila.inheco_sila_interface import SiLAState

from .odtc_sila_interface import (
  DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS,
  ODTCSiLAInterface,
)

# Buffer (seconds) added to device remaining duration (ExecuteMethod) or first_event_timeout (status commands) for timeout cap (fail faster than full lifetime).


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
    timeout: float = 10800.0,
    first_event_timeout_seconds: float = DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS,
  ):
    """Initialize ODTC backend.

    Args:
      odtc_ip: IP address of the ODTC device.
      variant: Well count (96 or 384). Device codes (960000, 384000, 3840000)
        are also accepted and normalized to 96/384.
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
      timeout: Max seconds to wait for execute_method with wait=True. Default 3 hours.
      first_event_timeout_seconds: Timeout for waiting for first DataEvent. Default 60s.
    """
    super().__init__()
    self._variant: ODTCVariant = normalize_variant(variant)
    self._simulation_mode: bool = False
    self._current_request_id: Optional[int] = None
    self._current_protocol: Optional[ODTCProtocol] = None
    self._timeout = timeout
    self._sila = ODTCSiLAInterface(
      machine_ip=odtc_ip,
      client_ip=client_ip,
      logger=logger,
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
  def simulation_mode(self) -> bool:
    """Whether the device is in simulation mode (from the last reset() call).

    Reflects the last simulation_mode passed to reset(); valid once that Reset
    has completed (or immediately if wait=True). Use this to check state without
    calling reset again.
    """
    return self._simulation_mode

  def _clear_execution_state(self) -> None:
    """Clear current execution state."""
    self._current_request_id = None
    self._current_protocol = None

  async def setup(
    self,
    full: bool = True,
    simulation_mode: bool = False,
    max_attempts: int = 10,
    retry_backoff_base_seconds: float = 1.0,
  ) -> None:
    """Prepare the ODTC connection.

    When full=True (default): full SiLA lifecycle (event receiver, Reset,
    Initialize, verify idle), with optional retry and exponential backoff.
    When full=False: only start the event receiver (reconnect without reset);
    use after session loss so a running method is not aborted.

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
    await self.reset(simulation_mode=simulation_mode)

    status = await self.request_status()
    self.logger.info(f"GetStatus returned state: {status.value!r}")

    if status == SiLAState.STANDBY:
      self.logger.info("Device is in standby state, calling Initialize...")
      await self.initialize()

      status_after_init = await self.request_status()

      if status_after_init == SiLAState.IDLE:
        self.logger.info("Device successfully initialized and is in idle state")
      else:
        raise RuntimeError(
          f"Device is not in idle state after Initialize. Expected {SiLAState.IDLE.value!r}, "
          f"but got {status_after_init.value!r}."
        )
    elif status == SiLAState.IDLE:
      self.logger.info("Device already in idle state after Reset")
    else:
      raise RuntimeError(
        f"Unexpected device state after Reset: {status.value!r}. "
        f"Expected {SiLAState.STANDBY.value!r} or {SiLAState.IDLE.value!r}."
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

  async def request_status(self) -> SiLAState:
    """Get device status state."""
    return await self._sila.request_status()

  async def initialize(self) -> None:
    """Initialize the device (SiLA: standby -> idle)."""
    await self._sila.send_command("Initialize")

  async def reset(self, simulation_mode: bool = False) -> None:
    """Reset the device (SiLA: startup -> standby, register event receiver)."""
    self._simulation_mode = simulation_mode
    await self._sila.send_command(
      "Reset",
      deviceId="ODTC",
      eventReceiverURI=self._sila.event_receiver_uri,
      simulationMode=simulation_mode,
    )
    self._sila._lock_id = None

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

  async def request_temperatures(self) -> ODTCSensorValues:
    """Read all temperature sensors.

    Returns:
      ODTCSensorValues with temperatures in °C.
    """
    resp = await self._request("ReadActualTemperature")
    sensor_xml = resp.get_parameter_string("SensorValues", allow_root_fallback=True)
    sensor_values = parse_sensor_values(sensor_xml)
    self.logger.debug("ReadActualTemperature: %s", sensor_values.format_compact())
    return sensor_values

  async def get_last_data(self) -> _NormalizedSiLAResponse:
    """Get temperature trace of last executed method"""
    return await self._request("GetLastData")

  async def execute_method(self, protocol: ODTCProtocol, wait: bool = False) -> None:
    """Execute a method or premethod on the device."""
    self._clear_execution_state()
    self._current_protocol = protocol

    fut, request_id = await self._sila.send_command_async("ExecuteMethod", methodName=protocol.name)
    self._current_request_id = request_id
    fut.add_done_callback(lambda _: self._clear_execution_state())

    if wait:
      await asyncio.wait_for(fut, timeout=self._timeout)

  async def stop_method(self) -> None:
    """Stop the currently running method (SiLA: StopMethod)."""
    await self._sila.send_command("StopMethod")

  # --- Method running and completion ---

  async def is_method_running(self) -> bool:
    """Check if a method is currently running.

    Uses GetStatus to check device state. Returns True if state is 'busy',
    indicating a method execution is in progress.

    Returns:
      True if method is running (state is 'busy'), False otherwise.
    """
    status = await self.request_status()
    return status == SiLAState.BUSY

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
    resolved = method_set.get(name)
    if resolved is None or resolved.kind == "premethod":
      return None
    return resolved

  def get_constraints(self) -> ODTCHardwareConstraints:
    """Get hardware constraints for this backend's variant.

    Returns:
      ODTCHardwareConstraints for the current variant (96 or 384-well).
    """
    return get_constraints(self._variant)

  # --- Protocol upload and run ---

  async def upload_protocol(
    self,
    protocol: ODTCProtocol,
    allow_overwrite: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
  ) -> None:
    """Upload an ODTCProtocol to the device."""
    if protocol.is_scratch:
      allow_overwrite = True
    if protocol.kind == "method":
      method_set = ODTCMethodSet(methods=[protocol], premethods=[])
    else:
      method_set = ODTCMethodSet(methods=[], premethods=[protocol])
    await self.upload_method_set(
      method_set,
      allow_overwrite=allow_overwrite,
      debug_xml=debug_xml,
      xml_output_path=xml_output_path,
    )

  async def run_stored_protocol(self, name: str, wait: bool = False) -> None:
    """Execute a stored protocol by name."""
    method_set = await self.get_method_set()
    resolved = method_set.get(name)
    if resolved is None:
      raise ValueError(f"Protocol '{name}' not found on device")
    await self.execute_method(resolved, wait=wait)

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
        existing_method = existing_method_set.get(method.name)
        if existing_method is not None:
          conflicts.append(
            f"Method '{method.name}' already exists as {_existing_item_type(existing_method)}"
          )

      # Check all premethod names (unified search)
      for premethod in method_set.premethods:
        existing_method = existing_method_set.get(premethod.name)
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

  async def open_lid(self):
    await self._sila.send_command("OpenDoor")

  async def close_lid(self):
    await self._sila.send_command("CloseDoor")

  async def set_block_temperature(
    self,
    temperature: List[float],
    lid_temperature: Optional[List[float]] = None,
    wait: bool = False,
    debug_xml: bool = False,
    xml_output_path: Optional[str] = None,
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
    """
    if not temperature:
      raise ValueError("At least one block temperature required")
    block_temp = temperature[0]
    if lid_temperature is not None:
      target_lid_temp = lid_temperature[0]
    else:
      constraints = self.get_constraints()
      target_lid_temp = constraints.max_lid_temp

    protocol = ODTCProtocol(
      variant=self._variant,
      plate_type=0,
      fluid_quantity=0,
      post_heating=False,
      start_block_temperature=0.0,
      start_lid_temperature=0.0,
      steps=[],
      pid_set=[ODTCPID(number=1)],
      kind="premethod",
      target_block_temperature=block_temp,
      target_lid_temperature=target_lid_temp,
    )
    await self.upload_protocol(
      protocol, allow_overwrite=True, debug_xml=debug_xml, xml_output_path=xml_output_path
    )
    await self.execute_method(protocol, wait=wait)

  async def set_lid_temperature(self, temperature: List[float]) -> None:
    """Not supported by ODTC. Use set_block_temperature(lid_temperature=...) instead."""
    raise NotImplementedError("ODTC does not support set_lid_temperature directly.")

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
    config: Optional[ODTCConfig] = None,
  ) -> None:
    """Execute a PLR Protocol on the ODTC.

    Args:
      protocol: Standard PLR Protocol.
      block_max_volume: Max volume in wells (uL), used to select fluid_quantity.
      config: Optional ODTCConfig overrides. If None, defaults are used with
        variant from this backend and fluid_quantity derived from block_max_volume.
    """
    if config is None:
      fluid_quantity = (
        volume_to_fluid_quantity(block_max_volume) if 0 < block_max_volume <= 100 else 1
      )
      config = ODTCConfig(variant=self._variant, fluid_quantity=fluid_quantity)
    odtc_protocol = protocol_to_odtc_protocol(protocol, config=config)
    await self.run_odtc_protocol(odtc_protocol)

  async def run_odtc_protocol(self, odtc_protocol: ODTCProtocol) -> None:
    """Execute a pre-built ODTCProtocol: pre-heat, upload, and start."""
    await self.set_block_temperature(
      temperature=[odtc_protocol.start_block_temperature],
      lid_temperature=[odtc_protocol.start_lid_temperature],
      wait=True,
    )
    await self.upload_protocol(odtc_protocol, allow_overwrite=True)
    await self.execute_method(odtc_protocol, wait=False)

  # --- Temperatures and lid/block status ---

  async def get_block_current_temperature(self) -> List[float]:
    sensor_values = await self.request_temperatures()
    return [sensor_values.mount]

  async def get_block_target_temperature(self) -> List[float]:
    """Not supported by ODTC."""
    raise RuntimeError("ODTC does not report block target temperature.")

  async def get_lid_current_temperature(self) -> List[float]:
    sensor_values = await self.request_temperatures()
    return [sensor_values.lid]

  async def get_lid_target_temperature(self) -> List[float]:
    """Not supported by ODTC."""
    raise RuntimeError("ODTC does not report lid target temperature.")

  async def get_lid_open(self) -> bool:
    """Not supported by ODTC."""
    raise NotImplementedError("ODTC does not report door status.")

  async def get_lid_status(self) -> LidStatus:
    """Get lid temperature status.

    Returns:
      LidStatus enum value.
    """
    # Simplified: if we can read temperature, assume it's holding
    try:
      await self.request_temperatures()
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
      await self.request_temperatures()
      return BlockStatus.HOLDING_AT_TARGET
    except Exception:
      return BlockStatus.IDLE

  # --- Progress and step/cycle (DataEvent) ---

  async def _get_progress(self, request_id: int) -> Optional[ODTCProgress]:
    """Get progress from latest DataEvent. Returns None if no protocol registered."""
    if self._current_protocol is None:
      return None
    events = self._sila.get_data_events(request_id)
    if not events:
      return None
    return build_progress_from_data_event(events[-1], odtc_protocol=self._current_protocol)

  async def get_progress_snapshot(self) -> Optional[ODTCProgress]:
    """Get current run progress. Returns None if no method is running."""
    if self._current_request_id is None:
      return None
    return await self._get_progress(self._current_request_id)

  async def get_hold_time(self) -> float:
    progress = await self.get_progress_snapshot()
    if progress is None:
      return 0.0
    return progress.remaining_hold_s if progress.remaining_hold_s is not None else 0.0

  async def get_current_cycle_index(self) -> int:
    progress = await self.get_progress_snapshot()
    if progress is None:
      return 0
    return progress.current_cycle_index if progress.current_cycle_index is not None else 0

  async def get_total_cycle_count(self) -> int:
    progress = await self.get_progress_snapshot()
    if progress is None:
      return 0
    return progress.total_cycle_count if progress.total_cycle_count is not None else 0

  async def get_current_step_index(self) -> int:
    progress = await self.get_progress_snapshot()
    if progress is None:
      return 0
    return progress.current_step_index if progress.current_step_index is not None else 0

  async def get_total_step_count(self) -> int:
    progress = await self.get_progress_snapshot()
    if progress is None:
      return 0
    return progress.total_step_count if progress.total_step_count is not None else 0
