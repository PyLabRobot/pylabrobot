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
  ODTCBackendParams,
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

  Device variant is fixed at construction time (via ODTC(variant=...)).
  Per-call compilation and execution config uses ODTCBackendParams (defined in
  model.py — the single source of truth for compilation defaults).
  Per-call temperature-control config uses SetBlockTempParams.
  Per-step PID overrides use StepParams.
  """

  @dataclass
  class SetBlockTempParams(BackendParams):
    """Per-call params for set_block_temperature(). Controls premethod compilation.

    Pass as backend_params to set_block_temperature(). Device variant is taken
    from the backend's construction-time variant (ODTC(variant=...)).
    """
    fluid_quantity: FluidQuantity = field(default=FluidQuantity.UL_30_TO_74)
    plate_type: int = 0
    pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])

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
    self._current_fut: Optional[asyncio.Future] = None
    self._last_target_temp_c: Optional[float] = None
    self._timeout: float = 10800.0  # 3-hour fallback; overridden by mcDuration

  async def _on_setup(self, backend_params: Optional[BackendParams] = None) -> None:
    self._current_request_id = None
    self._current_odtc_protocol = None
    self._current_fut = None
    self._last_target_temp_c = None

  def _clear_execution_state(self) -> None:
    self._current_request_id = None
    self._current_odtc_protocol = None
    self._current_fut = None
    self._last_target_temp_c = None

  # ------------------------------------------------------------------
  # Protocol helpers
  # ------------------------------------------------------------------

  def _resolve_odtc_protocol(
    self,
    protocol: Protocol,
    params: ODTCBackendParams,
    fluid_quantity: FluidQuantity,
  ) -> ODTCProtocol:
    """Return ODTCProtocol, compiling from generic Protocol if needed.

    fluid_quantity is passed explicitly (already resolved from params.fluid_quantity,
    the volume_ul capability arg, or the default) so that _resolve_odtc_protocol
    never has to consult params.fluid_quantity directly.
    """
    if isinstance(protocol, ODTCProtocol):
      # Pre-compiled protocol: fluid_quantity is already baked into the XML.
      # volume_ul and ODTCBackendParams.fluid_quantity have no effect here.
      return protocol
    return _from_protocol(
      protocol,
      variant=self._variant,
      fluid_quantity=fluid_quantity,
      plate_type=params.plate_type,
      post_heating=params.post_heating,
      pid_set=list(params.pid_set),
      name=params.name,
      apply_overshoot=params.apply_overshoot,
      default_heating_slope=params.default_heating_slope,
      default_cooling_slope=params.default_cooling_slope,
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
    self._current_fut = fut
    fut.add_done_callback(lambda _: self._clear_execution_state())

  # ------------------------------------------------------------------
  # ThermocyclerBackend abstract methods
  # ------------------------------------------------------------------

  async def run_protocol(
    self,
    protocol: Protocol,
    volume_ul: Optional[float] = None,
    backend_params: Optional[BackendParams] = None,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Upload and start a protocol. Non-blocking (fire-and-forget).

    Args:
      protocol: Protocol to compile and run.
      volume_ul: Maximum sample volume in wells (µL). Used to auto-select
        FluidQuantity when backend_params.fluid_quantity is not set explicitly.
        Overridden by an explicit fluid_quantity in backend_params.
      backend_params: ODTCBackendParams with compilation options. Defaults to
        ODTCBackendParams() when not provided or wrong type.
      dynamic_pre_method_duration: When True (default), the device reports live
        pre-heat remaining time. When False, uses the fixed 600 s estimate.
    """
    if not isinstance(backend_params, ODTCBackendParams):
      backend_params = ODTCBackendParams()

    if backend_params.fluid_quantity is not None:
      fq = backend_params.fluid_quantity
    elif volume_ul is not None:
      fq = volume_to_fluid_quantity(volume_ul)
    else:
      fq = FluidQuantity.UL_30_TO_74

    odtc = self._resolve_odtc_protocol(protocol, backend_params, fluid_quantity=fq)
    await self.upload_protocol(
      odtc,
      dynamic_pre_method_duration=dynamic_pre_method_duration,
    )
    await self._execute_method(odtc)

  async def set_block_temperature(
    self,
    temperature: float,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    """Set block temperature via a premethod protocol."""
    if not isinstance(backend_params, ODTCThermocyclerBackend.SetBlockTempParams):
      backend_params = ODTCThermocyclerBackend.SetBlockTempParams()
    lid_temp = 110.0  # default
    premethod = ODTCProtocol(
      stages=[],
      variant=self._variant,
      plate_type=backend_params.plate_type,
      fluid_quantity=backend_params.fluid_quantity,
      post_heating=False,
      start_block_temperature=0.0,
      start_lid_temperature=lid_temp,
      pid_set=list(backend_params.pid_set),
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

  async def wait_for_completion(
    self,
    timeout: Optional[float] = None,
    report_interval: float = 300.0,
  ) -> None:
    """Block until the running method/premethod completes.

    Returns immediately if no method is currently running. Uses
    asyncio.shield so that cancelling the caller does not cancel the
    underlying SiLA future (the device keeps running).

    Args:
      timeout: Maximum seconds to wait. Defaults to the backend timeout
        (3 hours). Pass a smaller value to fail faster.
      report_interval: Log a progress update every this many seconds via
        self.logger at INFO level (default 300 = 5 minutes). Pass 0 to
        disable periodic reporting.

    Raises:
      asyncio.TimeoutError: If the method does not complete within timeout.
    """
    if self._current_fut is None or self._current_fut.done():
      return
    effective = timeout if timeout is not None else self._timeout
    if not report_interval:
      await asyncio.wait_for(asyncio.shield(self._current_fut), timeout=effective)
      return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + effective
    while not self._current_fut.done():
      remaining = deadline - loop.time()
      if remaining <= 0:
        raise asyncio.TimeoutError()
      wait_s = min(report_interval, remaining)
      try:
        await asyncio.wait_for(asyncio.shield(self._current_fut), timeout=wait_s)
        return
      except asyncio.TimeoutError:
        pass
      progress = await self.request_progress()
      if progress is not None:
        self.logger.info(str(progress))

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
            protocol,
            variant=96,
            params=ODTCBackendParams(
                fluid_quantity=FluidQuantity.UL_30_TO_74,
                name="StandardPCR",
            ),
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
