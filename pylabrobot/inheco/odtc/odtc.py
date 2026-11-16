"""Inheco ODTC thermocycler operations over SiLA 1."""

from __future__ import annotations

import asyncio
import logging
import math
import xml.etree.ElementTree as ET

from pylabrobot.inheco.odtc.protocol import (
  DEFAULT_APPLY_OVERSHOOT,
  DEFAULT_PLATE_TYPE,
  DEFAULT_POST_HEATING,
  ODTCPID,
  ODTCMethod,
  ODTCMethodSet,
  ODTCPreMethod,
  ODTCProgress,
  ODTCProtocol,
  ODTCTemperatures,
  ODTCVariant,
  compile_protocol,
  validate_method,
  validate_temperature,
  validate_variant,
)
from pylabrobot.inheco.odtc.xml import (
  method_set_to_xml,
  parse_method_set,
  parse_progress,
  parse_sensor_values,
)
from pylabrobot.inheco.transport.sila import (
  InhecoSiLAInterface,
  SiLACommand,
  SiLAError,
  get_params,
  required_text,
)
from pylabrobot.resources import Coordinate, ResourceHolder

logger = logging.getLogger(__name__)


class ODTCProtocolStopped(RuntimeError):
  """The user stopped a protocol before it completed."""


class ODTC:
  """An ODTC, its plate location, and its connection and thermal execution state.

  Temperatures use degrees C, durations seconds, and sample volumes microliters.
  The plate holder's position must be calibrated before using it for robotic transfers.
  Both 96- and 384-well implementations require hardware verification.
  """

  _door_open: bool | None
  _prepared: tuple[float, float] | None
  _execution: SiLACommand | None
  _progress_by_request: dict[int, ODTCProgress]
  _workflow_lock: asyncio.Lock | None

  @property
  def door_open(self) -> bool | None:
    """Last command-confirmed door state; None means unknown, not a sensor reading."""
    return self._door_open

  def _on_data(self, request_id: int, data: str) -> None:
    """Store one measured sample, including callbacks that precede HTTP acceptance."""
    current = self._execution
    if self._sila.pending_command_name(request_id) != "ExecuteMethod" and (
      current is None or current.request_id != request_id
    ):
      return
    self._progress_by_request[request_id] = parse_progress(data)

  def _on_status(self, event: ET.Element) -> None:
    """Surface device-wide faults and invalidate state that can no longer be trusted."""
    description = event.find("{*}eventDescription")
    if description is None:
      raise ValueError("StatusEvent is missing eventDescription")
    if description.text and description.text.strip():
      description = ET.fromstring(description.text)
    state = (
      description.text
      if description.tag.rsplit("}", 1)[-1] == "DeviceState"
      else required_text(description, "DeviceState")
    )
    logger.debug("ODTC %s state: %s", self.name, state)
    if state is None or state.lower() not in ("errorhandling", "inerror"):
      return
    self._prepared = None
    self._door_open = None
    extensions = description.find("{*}Extensions")
    values = [child.text or "" for child in extensions] if extensions is not None else []
    code = int(values[1]) if len(values) >= 2 and values[1] else 9
    message = values[4] if len(values) >= 5 else f"Device entered {state}"
    logger.error("ODTC %s fault %d: %s", self.name, code, message)
    self._sila.fail_pending(SiLAError(code, message, "StatusEvent"))

  def __init__(
    self,
    name: str,
    odtc_ip: str,
    variant: ODTCVariant = 96,
    client_ip: str | None = None,
    *,
    odtc_port: int = 8080,
    callback_port: int = 0,
  ) -> None:
    self.name = name
    self.variant = validate_variant(variant)
    self.plate_holder = ResourceHolder(
      name=f"{name}_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    self._sila = InhecoSiLAInterface(
      machine_ip=odtc_ip,
      client_ip=client_ip,
      machine_port=odtc_port,
      callback_port=callback_port,
      on_data=self._on_data,
      on_status=self._on_status,
    )
    self._connected = False
    self._door_open = None
    self._prepared = None
    self._execution = None
    self._progress_by_request = {}
    self._workflow_lock = None
    self._stop_generation = 0

  def _require_connection(self) -> None:
    """Require a successfully initialized session."""
    if not self._connected:
      raise RuntimeError("ODTC is not set up")

  def _require_workflow_lock(self) -> asyncio.Lock:
    """Protect multi-command preparation/upload sequences, leaving reads and stop available."""
    self._require_connection()
    assert self._workflow_lock is not None
    return self._workflow_lock

  async def request_status(self) -> str:
    """Read the current SiLA state from the device."""
    response = await self._sila.send_command("GetStatus")
    state = response["GetStatusResponse"]["state"]
    if not isinstance(state, str):
      raise ValueError("GetStatus returned a non-string state")
    return state

  async def setup(self, *, simulation_mode: bool = False) -> None:
    """Connect, register callbacks, and initialize an idle or standby instrument.

    A busy device is left running and setup raises. Repeated setup on an established
    session is a no-op. Simulation mode is the firmware's simulation mode.
    """
    if self._connected:
      return
    logger.warning(
      "ODTC %d driver needs hardware verification; please report a verified run", self.variant
    )
    await self._sila.setup()
    try:
      state = await self.request_status()
      if state.lower() not in ("standby", "idle"):
        raise RuntimeError(f"ODTC is {state}; setup will not reset an active or faulted device")
      await self._sila.send_command(
        "Reset",
        deviceId=self.name,
        eventReceiverURI=self._sila.event_receiver_uri,
        simulationMode=simulation_mode,
      )
      state = await self.request_status()
      if state.lower() == "standby":
        await self._sila.send_command("Initialize")
        state = await self.request_status()
      if state.lower() != "idle":
        raise RuntimeError(f"ODTC did not initialize: {state}")
      self._door_open = None
      self._prepared = None
      self._execution = None
      self._progress_by_request = {}
      self._workflow_lock = asyncio.Lock()
      self._connected = True
      logger.info("ODTC %s connected", self.name)
    except BaseException:
      await self._sila.close()
      raise

  async def open_door(self) -> None:
    """Open the drawer and await command completion; remember only confirmed state."""
    self._require_connection()
    if self._door_open is True:
      return
    self._door_open = None
    await self._sila.send_command("OpenDoor")
    self._door_open = True
    logger.info("ODTC %s door opened", self.name)

  async def close_door(self) -> None:
    """Close the drawer and await command completion."""
    self._require_connection()
    if self._door_open is False:
      return
    self._door_open = None
    await self._sila.send_command("CloseDoor")
    self._door_open = False
    logger.info("ODTC %s door closed", self.name)

  async def request_temperatures(self) -> ODTCTemperatures:
    """Read all physical temperature sensors, including during a run."""
    self._require_connection()
    response = await self._sila.send_command("ReadActualTemperature")
    if not isinstance(response, ET.Element):
      raise ValueError("ReadActualTemperature did not return ResponseData")
    value = get_params(response, ["SensorValues"])["SensorValues"]
    if not isinstance(value, str):
      raise ValueError("SensorValues must contain XML text")
    return parse_sensor_values(value)

  async def request_block_temperature(self) -> float:
    """Read the block's mount temperature in degrees C."""
    return (await self.request_temperatures()).mount

  async def request_lid_temperature(self) -> float:
    """Read the heated lid temperature in degrees C."""
    return (await self.request_temperatures()).lid

  async def request_progress(self) -> ODTCProgress | None:
    """Return the latest measured sample for this execution, or None before any data arrives."""
    if self._execution is None:
      return None
    return self._progress_by_request.get(self._execution.request_id)

  async def request_method_set(self) -> ODTCMethodSet:
    """Retrieve the full stored methods, including native loops and PID settings."""
    self._require_connection()
    response = await self._sila.send_command("GetParameters")
    if not isinstance(response, ET.Element):
      raise ValueError("GetParameters did not return ResponseData")
    xml = get_params(response, ["MethodsXML"])["MethodsXML"]
    if not isinstance(xml, str):
      raise ValueError("MethodsXML must contain XML text")
    return parse_method_set(xml)

  async def request_protocol(self, name: str) -> ODTCMethod | None:
    """Retrieve a named compiled method, or None if it is absent."""
    methods = await self.request_method_set()
    return next((method for method in methods.methods if method.name == name), None)

  async def _upload(self, methods: ODTCMethodSet, *, dynamic_pre_method_duration: bool) -> None:
    """Upload explicit settings and methods without deleting unrelated device storage."""
    for method in methods.methods:
      if method.variant != self.variant:
        raise ValueError("Method variant does not match the ODTC")
    root = ET.Element("ParameterSet")
    param = ET.SubElement(root, "Parameter", name="MethodsXML", parameterType="String")
    ET.SubElement(param, "String").text = method_set_to_xml(methods)
    for name, value in {
      "DynamicPreMethodDuration": dynamic_pre_method_duration,
      "ExecuteMethodDataEvent": True,
      "MatchLidTemperatures": True,
    }.items():
      param = ET.SubElement(root, "Parameter", name=name, parameterType="Boolean")
      ET.SubElement(param, "Boolean").text = str(value).lower()
    await self._sila.send_command("SetParameters", paramsXML=ET.tostring(root, encoding="unicode"))

  async def _check_name(self, name: str, overwrite: bool) -> None:
    """Require an explicit overwrite for persistent methods, including premethod collisions."""
    if overwrite:
      return
    existing = await self.request_method_set()
    names = {method.name for method in existing.methods} | {
      method.name for method in existing.premethods
    }
    if name in names:
      raise ValueError(f"Method {name!r} already exists; pass overwrite=True to replace it")

  async def upload_protocol(self, method: ODTCMethod, *, overwrite: bool = False) -> None:
    """Persist a compiled method. Use compile_protocol to inspect the method before upload."""
    validate_method(method)
    async with self._require_workflow_lock():
      await self._check_name(method.name, overwrite)
      await self._upload(ODTCMethodSet(methods=[method]), dynamic_pre_method_duration=True)

  async def wait_for_completion(self, *, timeout: float | None = None) -> None:
    """Wait for the latest execution, retaining completed failures and shielding the device task.

    Cancellation or timeout stops only this wait. Call stop_protocol to stop the instrument.
    """
    execution = self._execution
    if execution is not None:
      await execution.wait(timeout)

  def _validate_timeout(self, timeout: float | None) -> None:
    """Reject invalid waits before starting a hardware operation."""
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
      raise ValueError("timeout must be finite and positive")

  def _check_stopped(self, generation: int) -> None:
    """Prevent a stopped preparation workflow from starting its following method."""
    if generation != self._stop_generation:
      raise ODTCProtocolStopped("ODTC workflow was stopped")

  async def _execute(self, name: str, timeout: float | None, generation: int) -> None:
    """Execute a named method and retain its result for subsequent waiters."""
    self._progress_by_request = {}
    self._check_stopped(generation)
    self._execution = await self._sila.start_command("ExecuteMethod", methodName=name)
    if generation != self._stop_generation and not self._execution.future.done():
      self._execution.future.set_exception(ODTCProtocolStopped("ODTC protocol was stopped"))
    self._check_stopped(generation)
    logger.info("ODTC %s executing %s", self.name, name)
    await self.wait_for_completion(timeout=timeout)
    self._check_stopped(generation)
    logger.info("ODTC %s completed %s", self.name, name)

  async def _prepare(
    self,
    temperature: float,
    lid_temperature: float,
    timeout: float | None,
    dynamic_pre_method_duration: bool,
    generation: int,
  ) -> None:
    """Establish homogeneous temperatures by waiting for a completed premethod."""
    validate_temperature(temperature, lid=False, variant=self.variant)
    validate_temperature(lid_temperature, lid=True, variant=self.variant)
    targets = (temperature, lid_temperature)
    self._check_stopped(generation)
    if self._prepared == targets:
      return
    self._prepared = None
    premethod = ODTCPreMethod("plr_currentPreMethod", temperature, lid_temperature)
    await self._upload(
      ODTCMethodSet(premethods=[premethod]), dynamic_pre_method_duration=dynamic_pre_method_duration
    )
    await self._execute(premethod.name, timeout, generation)
    self._prepared = targets

  async def prepare(
    self,
    temperature: float,
    lid_temperature: float,
    *,
    timeout: float | None = None,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Preheat before loading or running; return only after thermal preparation completes."""
    self._validate_timeout(timeout)
    generation = self._stop_generation
    async with self._require_workflow_lock():
      try:
        await self._prepare(
          temperature, lid_temperature, timeout, dynamic_pre_method_duration, generation
        )
      except BaseException:
        self._prepared = None
        raise

  async def _run(
    self, method: ODTCMethod, timeout: float | None, dynamic: bool, generation: int
  ) -> None:
    """Prepare matching thermal conditions and run an uploaded method."""
    try:
      await self._prepare(
        method.start_temperature, method.start_lid_temperature, timeout, dynamic, generation
      )
      self._prepared = None
      await self._execute(method.name, timeout, generation)
      if method.post_heating:
        final_step = method.steps[-1]
        self._prepared = (final_step.temperature, final_step.lid_temperature)
    except BaseException:
      self._prepared = None
      raise

  async def run_protocol(
    self,
    protocol: ODTCProtocol,
    *,
    volume: float,
    lid_temperature: float,
    plate_type: int = DEFAULT_PLATE_TYPE,
    post_heating: bool = DEFAULT_POST_HEATING,
    apply_overshoot: bool = DEFAULT_APPLY_OVERSHOOT,
    pid_set: list[ODTCPID] | None = None,
    overwrite: bool = False,
    timeout: float | None = None,
    dynamic_pre_method_duration: bool = True,
  ) -> None:
    """Compile, upload, prepare, and await a complete temperature protocol.

    A name in the profile selects persistent storage; otherwise the reserved
    plr_currentProtocol slot is replaced. Timeout applies to each execution wait.
    Waiting may be canceled without stopping the physical method; stop_protocol does that.
    """
    method = compile_protocol(
      protocol,
      variant=self.variant,
      volume=volume,
      lid_temperature=lid_temperature,
      plate_type=plate_type,
      post_heating=post_heating,
      apply_overshoot=apply_overshoot,
      pid_set=pid_set,
    )
    self._validate_timeout(timeout)
    generation = self._stop_generation
    async with self._require_workflow_lock():
      self._check_stopped(generation)
      await self._check_name(method.name, overwrite or protocol.name is None)
      await self._upload(
        ODTCMethodSet(methods=[method]), dynamic_pre_method_duration=dynamic_pre_method_duration
      )
      await self._run(method, timeout, dynamic_pre_method_duration, generation)

  async def run_stored_protocol(self, name: str, *, timeout: float | None = None) -> None:
    """Prepare and execute an existing method using its stored thermal settings."""
    self._validate_timeout(timeout)
    generation = self._stop_generation
    async with self._require_workflow_lock():
      self._check_stopped(generation)
      method = await self.request_protocol(name)
      if method is None:
        raise ValueError(f"No stored protocol named {name!r}")
      if method.variant != self.variant:
        raise ValueError("Stored method variant does not match the ODTC")
      await self._run(method, timeout, True, generation)

  async def stop_protocol(self) -> None:
    """Stop a running method or an idle post-method hold and await acknowledgement."""
    self._require_connection()
    self._stop_generation += 1
    self._prepared = None
    await self._sila.send_command("StopMethod")
    execution = self._execution
    if execution is not None and not execution.future.done():
      execution.future.set_exception(ODTCProtocolStopped("ODTC protocol was stopped"))
    logger.info("ODTC %s temperature control stopped", self.name)

  async def stop(self) -> None:
    """Stop temperature control and close transports, even if stopping the device fails."""
    try:
      if self._connected:
        await self.stop_protocol()
    finally:
      self._connected = False
      self._door_open = None
      self._prepared = None
      await self._sila.close()
      logger.info("ODTC %s disconnected", self.name)
