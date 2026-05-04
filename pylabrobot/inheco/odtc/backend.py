"""ODTCThermocyclerBackend — v1b1 CapabilityBackend for the ODTC."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.thermocycling.backend import ThermocyclerBackend
from pylabrobot.capabilities.thermocycling.standard import Protocol
from pylabrobot.inheco.scila.inheco_sila_interface import SiLAState

from .driver import ODTCDriver
from .model import (
  FluidQuantity,
  ODTCPID,
  ODTCMethodSet,
  ODTCProgress,
  ODTCProtocol,
  ODTCSensorValues,
  ODTCVariant,
  normalize_variant,
  volume_to_fluid_quantity,
)
from .protocol import (
  _from_protocol,
  build_progress_from_data_event,
)
from .xml import (
  method_set_to_xml,
  parse_method_set,
  parse_sensor_values,
)


class ODTCThermocyclerBackend(ThermocyclerBackend):
  """ThermocyclerBackend implementation for the Inheco ODTC.

  Uses ODTCDriver for SiLA communication. Accepts plain Protocol (compiles
  via ODTCProtocol.from_protocol) or ODTCProtocol directly (used as-is).

  Device config is passed via RunProtocolParams; per-step overrides via
  StepParams attached to Step.backend_params.
  """

  @dataclass
  class RunProtocolParams(BackendParams):
    """ODTC-specific parameters for run_protocol / execute_method.

    Replaces the old ODTCConfig. Pass as backend_params to run_protocol().
    """
    variant: ODTCVariant = 96
    fluid_quantity: FluidQuantity = field(default=FluidQuantity.UL_30_TO_74)
    plate_type: int = 0
    post_heating: bool = True
    pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])
    dynamic_pre_method_duration: bool = True
    default_heating_slope: Optional[float] = None   # None = hardware max
    default_cooling_slope: Optional[float] = None   # None = hardware max
    name: Optional[str] = None
    creator: Optional[str] = None
    apply_overshoot: bool = True
    """If True (default), auto-compute overshoot for steps without an explicit Ramp.overshoot.
    If False, no overshoot is applied regardless of ramp rate or fluid quantity.
    Explicit Ramp.overshoot values are always honoured either way."""

  @dataclass
  class StepParams(BackendParams):
    """Per-step ODTC overrides. Attach to Step.backend_params."""
    pid_number: int = 1

  def __init__(
    self,
    driver: ODTCDriver,
    variant: int = 96,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    self._driver = driver
    self._variant: ODTCVariant = normalize_variant(variant)
    self.logger = logger or logging.getLogger(__name__)
    self._current_request_id: Optional[int] = None
    self._current_odtc_protocol: Optional[ODTCProtocol] = None
    self._last_target_temp_c: Optional[float] = None
    self._timeout: float = 10800.0  # 3-hour fallback; overridden by mcDuration

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    self._current_request_id = None
    self._current_odtc_protocol = None
    self._last_target_temp_c = None

  def _clear_execution_state(self) -> None:
    self._current_request_id = None
    self._current_odtc_protocol = None
    self._last_target_temp_c = None

  # ------------------------------------------------------------------
  # Protocol helpers
  # ------------------------------------------------------------------

  def _resolve_odtc_protocol(
    self,
    protocol: Protocol,
    params: "ODTCThermocyclerBackend.RunProtocolParams",
  ) -> ODTCProtocol:
    """Return ODTCProtocol, compiling from generic Protocol if needed."""
    if isinstance(protocol, ODTCProtocol):
      return protocol
    return _from_protocol(
      protocol,
      variant=params.variant,
      fluid_quantity=params.fluid_quantity,
      plate_type=params.plate_type,
      post_heating=params.post_heating,
      pid_set=list(params.pid_set),
      name=params.name,
      default_heating_slope=params.default_heating_slope,
      default_cooling_slope=params.default_cooling_slope,
      apply_overshoot=params.apply_overshoot,
      creator=params.creator,
    )

  async def _upload_method_set(
    self,
    method_set: ODTCMethodSet,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Upload a MethodSet to the device via SetParameters."""
    method_set_xml = method_set_to_xml(method_set)
    param_set = ET.Element("ParameterSet")
    param = ET.SubElement(param_set, "Parameter", parameterType="String", name="MethodsXML")
    ET.SubElement(param, "String").text = method_set_xml
    dpm_param = ET.SubElement(param_set, "Parameter", parameterType="Boolean", name="DynamicPreMethodDuration")
    ET.SubElement(dpm_param, "Boolean").text = "true" if dynamic_pre_method_duration else "false"
    params_xml = ET.tostring(param_set, encoding="unicode", xml_declaration=False)
    await self._driver.send_command("SetParameters", paramsXML=params_xml)

  # ------------------------------------------------------------------
  # Execution helper
  # ------------------------------------------------------------------

  async def _execute_method(self, odtc_protocol: ODTCProtocol) -> None:
    """Clear state, start ExecuteMethod, and register tracking callbacks.

    All fire-and-forget execution paths funnel through here so that
    _clear_execution_state, protocol/request-id recording, and the done
    callback are always applied in the correct order.
    """
    self._clear_execution_state()
    self._current_odtc_protocol = odtc_protocol
    fut, request_id = await self._driver.send_command_async(
      "ExecuteMethod", methodName=odtc_protocol.name
    )
    self._current_request_id = request_id
    fut.add_done_callback(lambda _: self._clear_execution_state())

  # ------------------------------------------------------------------
  # ThermocyclerBackend abstract methods
  # ------------------------------------------------------------------

  async def run_protocol(
    self,
    protocol: Protocol,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Upload and start a protocol. Non-blocking (fire-and-forget)."""
    if not isinstance(backend_params, ODTCThermocyclerBackend.RunProtocolParams):
      backend_params = ODTCThermocyclerBackend.RunProtocolParams(variant=self._variant)

    odtc = self._resolve_odtc_protocol(protocol, backend_params)
    await self.upload_protocol(
      odtc,
      dynamic_pre_method_duration=backend_params.dynamic_pre_method_duration,
    )
    await self._execute_method(odtc)

  async def set_block_temperature(
    self,
    temperature: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set block temperature via a premethod protocol."""
    params = (
      backend_params
      if isinstance(backend_params, ODTCThermocyclerBackend.RunProtocolParams)
      else ODTCThermocyclerBackend.RunProtocolParams(variant=self._variant)
    )
    lid_temp = 110.0  # default
    premethod = ODTCProtocol(
      stages=[],
      variant=params.variant,
      plate_type=params.plate_type,
      fluid_quantity=params.fluid_quantity,
      post_heating=False,
      start_block_temperature=0.0,
      start_lid_temperature=lid_temp,
      pid_set=list(params.pid_set),
      kind="premethod",
      is_scratch=True,
      target_block_temperature=temperature,
      target_lid_temperature=lid_temp,
    )
    await self.upload_protocol(premethod)
    await self._execute_method(premethod)

  async def deactivate_block(self, backend_params: Optional[BackendParams] = None) -> None:
    await self._driver.send_command("StopMethod")
    self._clear_execution_state()

  async def request_block_temperature(self) -> float:
    sensor_values = await self.request_temperatures()
    return sensor_values.mount

  async def request_lid_temperature(self) -> float:
    sensor_values = await self.request_temperatures()
    return sensor_values.lid

  async def request_progress(self) -> Optional[Any]:
    if self._current_request_id is None or self._current_odtc_protocol is None:
      return None
    events = self._driver.get_data_events(self._current_request_id)
    if not events:
      return None
    progress = build_progress_from_data_event(
      events[-1],
      odtc_protocol=self._current_odtc_protocol,
      last_target_temp_c=self._last_target_temp_c,
    )
    if progress.target_temp_c is not None:
      self._last_target_temp_c = progress.target_temp_c
    return progress

  async def stop_protocol(self, backend_params: Optional[BackendParams] = None) -> None:
    await self._driver.send_command("StopMethod")
    self._clear_execution_state()

  # ------------------------------------------------------------------
  # ODTC-specific public methods (available on the backend directly)
  # ------------------------------------------------------------------

  async def request_temperatures(self) -> ODTCSensorValues:
    resp = await self._driver.send_command("ReadActualTemperature")
    if resp is None:
      raise RuntimeError("ReadActualTemperature returned no data")
    if isinstance(resp, dict):
      param = (resp.get("ReadActualTemperatureResponse", {})
               .get("ResponseData", {})
               .get("Parameter", {}))
      if isinstance(param, list):
        param = next((p for p in param if p.get("name") == "SensorValues"), {})
      xml_str = param.get("String", "")
    else:
      import xml.etree.ElementTree as ET2
      string_elem = resp.find(".//String")
      xml_str = string_elem.text if string_elem is not None else ""
    if not xml_str:
      raise RuntimeError("Could not extract SensorValues from ReadActualTemperature response")
    return parse_sensor_values(xml_str)

  async def request_status(self) -> SiLAState:
    return await self._driver.request_status()

  async def get_method_set(self) -> ODTCMethodSet:
    resp = await self._driver.send_command("GetParameters")
    if resp is None:
      raise RuntimeError("GetParameters returned no data")
    if isinstance(resp, dict):
      param = (resp.get("GetParametersResponse", {})
               .get("ResponseData", {})
               .get("Parameter", {}))
      if isinstance(param, list):
        param = next((p for p in param if p.get("name") == "MethodsXML"), {})
      xml_str = param.get("String", "")
    else:
      string_elem = resp.find(".//String")
      xml_str = string_elem.text if string_elem is not None else ""
    if not xml_str:
      raise RuntimeError("Could not extract MethodsXML from GetParameters response")
    return parse_method_set(xml_str)

  async def get_protocol(self, name: str) -> Optional[ODTCProtocol]:
    """Fetch a stored runnable method by name.

    Returns None if the name does not exist or refers to a premethod.

    Args:
      name: Protocol name to retrieve.
    """
    method_set = await self.get_method_set()
    resolved = method_set.get(name)
    if resolved is None or resolved.kind == "premethod":
      return None
    return resolved

  async def upload_method_set(
    self,
    method_set: ODTCMethodSet,
    allow_overwrite: bool = False,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Upload a MethodSet to the device.

    Args:
      method_set: The method set to upload.
      allow_overwrite: If False (default), raises ValueError when any method
        or premethod name already exists on the device. If True, overwrites.
      dynamic_pre_method_duration: When True, device reports live pre-heat
        remaining time. When False, uses the fixed 600 s estimate.

    Raises:
      ValueError: On name conflicts when allow_overwrite=False.
    """
    if not allow_overwrite:
      existing = await self.get_method_set()
      conflicts = [
        m.name for m in method_set.methods + method_set.premethods
        if existing.get(m.name) is not None
      ]
      if conflicts:
        raise ValueError(
          f"Name conflicts on device: {conflicts}. "
          f"Pass allow_overwrite=True to overwrite."
        )
    await self._upload_method_set(method_set, dynamic_pre_method_duration)

  async def upload_protocol(
    self,
    odtc_protocol: ODTCProtocol,
    allow_overwrite: bool = False,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Upload a single ODTCProtocol to the device for persistent storage.

    Scratch protocols (is_scratch=True) bypass the conflict check.
    Named protocols persist across device Reset and can be run later by
    name via run_stored_protocol().

    Typical workflow::

        odtc_p = ODTCProtocol.from_protocol(
            protocol, name="StandardPCR",
            fluid_quantity=FluidQuantity.UL_30_TO_74,
        )
        await odtc.tc.backend.upload_protocol(odtc_p)
        # later:
        await odtc.tc.backend.run_stored_protocol("StandardPCR")

    Args:
      odtc_protocol: Compiled protocol to upload.
      allow_overwrite: If False, raises ValueError when a protocol with
        the same name already exists. Scratch methods always overwrite.
      dynamic_pre_method_duration: Passed to the SetParameters command.

    Raises:
      ValueError: On name conflict when allow_overwrite=False.
    """
    if odtc_protocol.is_scratch:
      allow_overwrite = True
    if odtc_protocol.kind == "method":
      ms = ODTCMethodSet(methods=[odtc_protocol])
    else:
      ms = ODTCMethodSet(premethods=[odtc_protocol])
    await self.upload_method_set(
      ms,
      allow_overwrite=allow_overwrite,
      dynamic_pre_method_duration=dynamic_pre_method_duration,
    )

  async def run_stored_protocol(self, name: str) -> None:
    """Execute a named protocol already stored on the device. Fire-and-forget.

    The protocol must have been uploaded previously via upload_protocol() or
    run_protocol() with a persistent name. Does not re-upload.

    Args:
      name: Name of the stored protocol to execute.

    Raises:
      ValueError: If no runnable protocol with the given name exists on the device.
    """
    resolved = await self.get_protocol(name)
    if resolved is None:
      raise ValueError(
        f"Protocol {name!r} not found on device. "
        f"Upload it first with upload_protocol()."
      )
    await self._execute_method(resolved)
