"""Inheco ODTC thermocycler backend and device."""

import asyncio
import datetime
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
  TemperatureControllerBackend,
)
from pylabrobot.capabilities.thermocycling import (
  Protocol,
  ThermocyclingBackend,
  ThermocyclingCapability,
)
from pylabrobot.device import Device, Driver
from pylabrobot.inheco.scila.inheco_sila_interface import InhecoSiLAInterface, SiLAError
from pylabrobot.resources import Coordinate, ResourceHolder
from pylabrobot.serializer import SerializableMixin

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_number(n: Any) -> str:
  if n is None:
    return "0"
  try:
    f = float(n)
    return str(int(f)) if f.is_integer() else str(f)
  except (ValueError, TypeError):
    return str(n)


def _recursive_find_key(data: Any, key: str) -> Any:
  if isinstance(data, dict):
    if key in data:
      return data[key]
    for v in data.values():
      item = _recursive_find_key(v, key)
      if item is not None:
        return item
  elif isinstance(data, list):
    for v in data:
      item = _recursive_find_key(v, key)
      if item is not None:
        return item
  elif hasattr(data, "find"):
    node = data.find(f".//{key}")
    if node is not None:
      return node.text
    if str(data.tag).endswith(key):
      return data.text
  return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class ODTCDriver(Driver):
  """Low-level SiLA driver for the Inheco ODTC."""

  def __init__(self, ip: str, client_ip: Optional[str] = None):
    super().__init__()
    self._sila = InhecoSiLAInterface(client_ip=client_ip, machine_ip=ip)

  async def setup(self):
    await self._sila.setup()
    await self._reset_and_initialize()

  async def stop(self):
    await self._sila.close()

  async def _reset_and_initialize(self) -> None:
    try:
      event_uri = f"http://{self._sila._client_ip}:{self._sila.bound_port}/"
      await self._sila.send_command(
        command="Reset", deviceId="ODTC", eventReceiverURI=event_uri, simulationMode=False
      )
      await self._sila.send_command("Initialize")
    except Exception as e:
      logger.warning("Warning during ODTC initialization: %s", e)

  async def wait_for_idle(self, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
      root = await self._sila.send_command("GetStatus")
      st = _recursive_find_key(root, "state")
      if st and st in ["idle", "standby"]:
        return
      await asyncio.sleep(1)
    raise RuntimeError("Timeout waiting for ODTC idle state")

  async def send_command(self, command: str, **kwargs):
    return await self._sila.send_command(command, **kwargs)

  async def get_sensor_data(self, cache: dict) -> Dict[str, float]:
    """Read sensor data. Uses cache dict for 2-second caching."""
    if time.time() - cache.get("_time", 0) < 2.0 and cache.get("_data"):
      return cache["_data"]

    try:
      root = await self._sila.send_command("ReadActualTemperature")
      embedded_xml = _recursive_find_key(root, "String")

      if embedded_xml and isinstance(embedded_xml, str):
        sensor_root = ET.fromstring(embedded_xml)
        data = {}
        for child in sensor_root:
          if child.tag and child.text:
            try:
              data[child.tag] = float(child.text) / 100.0
            except ValueError:
              pass
        cache["_data"] = data
        cache["_time"] = time.time()
        return data
    except Exception as e:
      logger.error("Error reading sensor data: %s", e)
    return cache.get("_data", {})


# ---------------------------------------------------------------------------
# Capability backends
# ---------------------------------------------------------------------------


class ODTCBlockBackend(TemperatureControllerBackend):
  """Block temperature controller for the ODTC."""

  def __init__(self, driver: ODTCDriver):
    self._driver = driver
    self._target: Optional[float] = None
    self._lid_target: Optional[float] = None
    self._sensor_cache: Dict[str, Any] = {}

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    self._target = temperature
    lid = self._lid_target if self._lid_target is not None else 105.0
    await self._run_pre_method(temperature, lid)

  async def get_current_temperature(self) -> float:
    data = await self._driver.get_sensor_data(self._sensor_cache)
    return data.get("Mount", 0.0)

  async def deactivate(self):
    await self._driver.send_command("StopMethod")

  async def _run_pre_method(self, block_temp: float, lid_temp: float, dynamic_time: bool = True):
    now = datetime.datetime.now().astimezone()
    method_name = f"PLR_Hold_{now.strftime('%Y%m%d_%H%M%S')}"

    methods_xml = (
      f'<?xml version="1.0" encoding="utf-8"?>'
      f"<MethodSet>"
      f"<DeleteAllMethods>false</DeleteAllMethods>"
      f'<PreMethod methodName="{method_name}" creator="PLR" dateTime="{now.isoformat()}">'
      f"<TargetBlockTemperature>{_format_number(block_temp)}</TargetBlockTemperature>"
      f"<TargetLidTemp>{_format_number(lid_temp)}</TargetLidTemp>"
      f"<DynamicPreMethodDuration>{'true' if dynamic_time else 'false'}</DynamicPreMethodDuration>"
      f"</PreMethod>"
      f"</MethodSet>"
    )

    ps = ET.Element("ParameterSet")
    pm = ET.SubElement(ps, "Parameter", name="MethodsXML")
    ET.SubElement(pm, "String").text = methods_xml
    params_xml = ET.tostring(ps, encoding="unicode")

    await self._driver.send_command("StopMethod")
    await self._driver.wait_for_idle()
    await self._driver.send_command("SetParameters", paramsXML=params_xml)
    await self._driver.send_command("ExecuteMethod", methodName=method_name)


class ODTCLidBackend(TemperatureControllerBackend):
  """Lid temperature controller for the ODTC."""

  def __init__(self, driver: ODTCDriver, block_backend: ODTCBlockBackend):
    self._driver = driver
    self._block = block_backend
    self._sensor_cache: Dict[str, Any] = {}

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    self._block._lid_target = temperature
    block = self._block._target if self._block._target is not None else 25.0
    await self._block._run_pre_method(block, temperature)

  async def get_current_temperature(self) -> float:
    data = await self._driver.get_sensor_data(self._sensor_cache)
    return data.get("Lid", 0.0)

  async def deactivate(self):
    raise NotImplementedError("ODTC lid cannot be deactivated independently.")


@dataclass
class ODTCRunProtocolParams(BackendParams):
  """ODTC-specific parameters for run_protocol."""

  start_block_temperature: float = 25.0
  start_lid_temperature: float = 30.0
  post_heating: bool = True
  method_name: Optional[str] = None


class ODTCThermocyclingBackend(ThermocyclingBackend):
  """Thermocycling backend for the ODTC."""

  def __init__(self, driver: ODTCDriver):
    self._driver = driver

  async def _on_stop(self):
    await self._driver.send_command("StopMethod")
    await super()._on_stop()

  async def open_lid(self) -> None:
    await self._driver.send_command("OpenDoor")

  async def close_lid(self) -> None:
    await self._driver.send_command("CloseDoor")

  async def get_lid_open(self) -> bool:
    raise NotImplementedError()

  async def run_protocol(self, protocol: Protocol, block_max_volume: float,
                         backend_params: Optional[SerializableMixin] = None) -> None:
    if isinstance(backend_params, ODTCRunProtocolParams):
      params = backend_params
    else:
      params = ODTCRunProtocolParams()
    method_xml, method_name = _generate_method_xml(
      protocol=protocol,
      block_max_volume=block_max_volume,
      start_block_temperature=params.start_block_temperature,
      start_lid_temperature=params.start_lid_temperature,
      post_heating=params.post_heating,
      method_name=params.method_name,
    )

    ps = ET.Element("ParameterSet")
    pm = ET.SubElement(ps, "Parameter", name="MethodsXML")
    ET.SubElement(pm, "String").text = method_xml
    params_xml = ET.tostring(ps, encoding="unicode")

    await self._driver.send_command("SetParameters", paramsXML=params_xml)
    try:
      await self._driver.send_command("ExecuteMethod", methodName=method_name)
    except SiLAError as e:
      if e.code == 12:  # SuccessWithWarning
        logger.warning("[ODTC Warning] %s", e.message)
      else:
        raise

  async def get_hold_time(self) -> float:
    raise NotImplementedError()

  async def get_current_cycle_index(self) -> int:
    raise NotImplementedError()

  async def get_total_cycle_count(self) -> int:
    raise NotImplementedError()

  async def get_current_step_index(self) -> int:
    raise NotImplementedError()

  async def get_total_step_count(self) -> int:
    raise NotImplementedError()


# ---------------------------------------------------------------------------
# XML generation helper
# ---------------------------------------------------------------------------


def _generate_method_xml(
  protocol: Protocol,
  block_max_volume: float,
  start_block_temperature: float,
  start_lid_temperature: float,
  post_heating: bool,
  method_name: Optional[str] = None,
  **kwargs,
) -> tuple:
  if not method_name:
    method_name = f"PLR_Protocol_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

  if block_max_volume < 30.0:
    fluid_quantity = "0"
  elif block_max_volume < 75.0:
    fluid_quantity = "1"
  else:
    fluid_quantity = "2"

  now = datetime.datetime.now().astimezone()
  now_str = now.isoformat()

  root = ET.Element("MethodSet")
  ET.SubElement(root, "DeleteAllMethods").text = "false"

  method_elem = ET.SubElement(
    root, "Method", methodName=method_name, creator="PyLabRobot", dateTime=now_str
  )
  ET.SubElement(method_elem, "Variant").text = "960000"
  ET.SubElement(method_elem, "PlateType").text = "0"
  ET.SubElement(method_elem, "FluidQuantity").text = fluid_quantity
  ET.SubElement(method_elem, "PostHeating").text = "true" if post_heating else "false"
  ET.SubElement(method_elem, "StartBlockTemperature").text = _format_number(start_block_temperature)
  ET.SubElement(method_elem, "StartLidTemperature").text = _format_number(start_lid_temperature)

  def_slope = _format_number(kwargs.get("slope", "4.4"))
  def_os_slope1 = _format_number(kwargs.get("overshoot_slope1", "0.1"))
  def_os_temp = _format_number(kwargs.get("overshoot_temperature", "0"))
  def_os_time = _format_number(kwargs.get("overshoot_time", "0"))
  def_os_slope2 = _format_number(kwargs.get("overshoot_slope2", "0.1"))
  pid_number = _format_number(kwargs.get("pid_number", "1"))

  step_counter = 1
  for stage in protocol.stages:
    if not stage.steps:
      continue
    start_of_stage = step_counter

    for i, step in enumerate(stage.steps):
      b_temp = step.temperature[0] if step.temperature else 25
      l_temp = start_lid_temperature
      duration = step.hold_seconds
      s_slope = _format_number(step.rate) if step.rate is not None else def_slope

      s = ET.SubElement(method_elem, "Step")
      ET.SubElement(s, "Number").text = str(step_counter)
      ET.SubElement(s, "Slope").text = s_slope
      ET.SubElement(s, "PlateauTemperature").text = _format_number(b_temp)
      ET.SubElement(s, "PlateauTime").text = _format_number(duration)
      ET.SubElement(s, "OverShootSlope1").text = def_os_slope1
      ET.SubElement(s, "OverShootTemperature").text = def_os_temp
      ET.SubElement(s, "OverShootTime").text = def_os_time
      ET.SubElement(s, "OverShootSlope2").text = def_os_slope2

      if i == len(stage.steps) - 1 and stage.repeats > 1:
        ET.SubElement(s, "GotoNumber").text = str(start_of_stage)
        ET.SubElement(s, "LoopNumber").text = str(stage.repeats - 1)
      else:
        ET.SubElement(s, "GotoNumber").text = "0"
        ET.SubElement(s, "LoopNumber").text = "0"

      ET.SubElement(s, "PIDNumber").text = pid_number
      ET.SubElement(s, "LidTemp").text = _format_number(l_temp)
      step_counter += 1

  pid_set = ET.SubElement(method_elem, "PIDSet")
  pid = ET.SubElement(pid_set, "PID", number=pid_number)
  defaults = {
    "PHeating": "60",
    "PCooling": "80",
    "IHeating": "250",
    "ICooling": "100",
    "DHeating": "10",
    "DCooling": "10",
    "PLid": "100",
    "ILid": "70",
  }
  for k, v in defaults.items():
    val = kwargs.get(k, v)
    ET.SubElement(pid, k).text = _format_number(val)

  xml_str = '<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(root, encoding="unicode")
  return xml_str, method_name


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class ODTC(ResourceHolder, Device):
  """Inheco ODTC thermocycler."""

  def __init__(
    self,
    name: str,
    ip: str,
    client_ip: Optional[str] = None,
    child_location: Coordinate = Coordinate.zero(),
    size_x: float = 150.0,
    size_y: float = 150.0,
    size_z: float = 200.0,
  ):
    self._driver = ODTCDriver(ip=ip, client_ip=client_ip)

    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category="thermocycler",
      model="Inheco ODTC",
    )
    Device.__init__(self, backend=self._driver)

    block_be = ODTCBlockBackend(self._driver)
    lid_be = ODTCLidBackend(self._driver, block_backend=block_be)
    tc_be = ODTCThermocyclingBackend(self._driver)

    self.block = TemperatureControlCapability(backend=block_be)
    self.lid = TemperatureControlCapability(backend=lid_be)
    self.thermocycling = ThermocyclingCapability(backend=tc_be, block=self.block, lid=self.lid)
    self._capabilities = [self.block, self.lid, self.thermocycling]

  def serialize(self) -> dict:
    return {**ResourceHolder.serialize(self), **Device.serialize(self)}
