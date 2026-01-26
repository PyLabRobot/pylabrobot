import asyncio
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
import time
import datetime

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Protocol
from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface, SiLAError


def _format_number(n: Any) -> str:
    if n is None: return "0"
    try:
        f = float(n)
        return str(int(f)) if f.is_integer() else str(f)
    except:
        return str(n)

def _recursive_find_key(data: Any, key: str) -> Any:
  if isinstance(data, dict):
    if key in data: return data[key]
    for v in data.values():
      item = _recursive_find_key(v, key)
      if item is not None: return item
  elif isinstance(data, list):
    for v in data:
      item = _recursive_find_key(v, key)
      if item is not None: return item
  elif hasattr(data, "find"):
    node = data.find(f".//{key}")
    if node is not None:
        return node.text
    if str(data.tag).endswith(key):
        return data.text
  return None


class ODTCBackend(ThermocyclerBackend):
  def __init__(self, ip: str, client_ip: Optional[str] = None) -> None:
    self._sila_interface = InhecoSiLAInterface(client_ip=client_ip, machine_ip=ip)
    self._block_target_temp: Optional[float] = None
    self._lid_target_temp: Optional[float] = None
    self._current_sensors: Dict[str, float] = {}
    self._temp_update_time = 0

  async def setup(self) -> None:
    await self._sila_interface.setup()
    await self._reset_and_initialize()

  async def stop(self):
    await self._sila_interface.close()

  async def _reset_and_initialize(self) -> None:
    try:
      event_uri = f"http://{self._sila_interface._client_ip}:{self._sila_interface.bound_port}/"
      await self._sila_interface.send_command(
        command="Reset", deviceId="ODTC", eventReceiverURI=event_uri, simulationMode=False
      )
      await self._sila_interface.send_command("Initialize")
    except Exception as e:
      print(f"Warning during ODTC initialization: {e}")

  async def _wait_for_idle(self, timeout=30):
    """Wait until device state is not Busy."""
    start = time.time()
    while time.time() - start < timeout:
      root = await self._sila_interface.send_command("GetStatus")
      st = _recursive_find_key(root, "state")
      if st and st in ["idle", "standby"]:
        return
      await asyncio.sleep(1)
    raise RuntimeError("Timeout waiting for ODTC idle state")

  # -------------------------------------------------------------------------
  # Lid
  # -------------------------------------------------------------------------

  async def open_lid(self):
    await self._sila_interface.send_command("OpenDoor")

  async def close_lid(self):
    await self._sila_interface.send_command("CloseDoor")

  async def get_lid_open(self) -> bool:
    raise NotImplementedError()

  async def get_lid_status(self) -> LidStatus:
    raise NotImplementedError()

  # -------------------------------------------------------------------------
  # Temperature Helpers
  # -------------------------------------------------------------------------

  async def get_sensor_data(self) -> Dict[str, float]:
    """
    Get all sensor data from the device.
    Returns a dictionary with keys: 'Mount', 'Mount_Monitor', 'Lid', 'Lid_Monitor', 
    'Ambient', 'PCB', 'Heatsink', 'Heatsink_TEC'. 
    Values are in degrees Celsius.
    """
    if time.time() - self._temp_update_time < 2.0 and self._current_sensors:
      return self._current_sensors

    try:
      root = await self._sila_interface.send_command("ReadActualTemperature")
      
      embedded_xml = _recursive_find_key(root, "String")

      if embedded_xml and isinstance(embedded_xml, str):
             sensor_root = ET.fromstring(embedded_xml)
             
             data = {}
             for child in sensor_root:
               if child.tag and child.text:
                  try:
                    # Values are integers scaled by 100 (3700 -> 37.0 C)
                    data[child.tag] = float(child.text) / 100.0
                  except ValueError:
                    pass
             
             self._current_sensors = data
             self._temp_update_time = time.time()
             return self._current_sensors
    except Exception as e:
      print(f"Error reading sensor data: {e}")
      pass
    return self._current_sensors

  async def _run_pre_method(self, block_temp: float, lid_temp: float, dynamic_time: bool = True):
    """
    Define and run a PreMethod (Hold) used for setting constant temperature.
    WARNING: ODTC pre-methods take 7-10 minutes to pre-warm evenly the block and lid before a run.
    This command is not ideal for quick temperature changes.
    dynamic_time: if True, method will complete in less than 10 minutes (like 7)
      if False, command holds temp for 10 minutes before proceeding
    """
    now = datetime.datetime.now().astimezone()
    method_name = f"PLR_Hold_{now.strftime('%Y%m%d_%H%M%S')}"

    methods_xml = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<MethodSet>'
        f'<DeleteAllMethods>false</DeleteAllMethods>'
        f'<PreMethod methodName="{method_name}" creator="PLR" dateTime="{now.isoformat()}">'
        f'<TargetBlockTemperature>{_format_number(block_temp)}</TargetBlockTemperature>'
        f'<TargetLidTemp>{_format_number(lid_temp)}</TargetLidTemp>'
        f'<DynamicPreMethodDuration>{"true" if dynamic_time else "false"}</DynamicPreMethodDuration>'
        f'</PreMethod>'
        f'</MethodSet>'
    )
    
    ps = ET.Element("ParameterSet")
    pm = ET.SubElement(ps, "Parameter", name="MethodsXML")
    ET.SubElement(pm, "String").text = methods_xml
    params_xml = ET.tostring(ps, encoding="unicode")
    
    await self.stop_method()
    await self._wait_for_idle()
    
    await self._sila_interface.send_command("SetParameters", paramsXML=params_xml)
    await self._sila_interface.send_command("ExecuteMethod", methodName=method_name)

  # -------------------------------------------------------------------------
  # Block Temperature
  # -------------------------------------------------------------------------

  async def set_block_temperature(self, temperature: List[float], dynamic_time: bool = True):
    if not temperature: return
    self._block_target_temp = temperature[0]
    lid = self._lid_target_temp if self._lid_target_temp is not None else 105.0 
    await self._run_pre_method(self._block_target_temp, lid, dynamic_time=dynamic_time)

  async def deactivate_block(self):
    await self.stop_method()

  async def get_block_current_temperature(self) -> List[float]:
    temps = await self.get_sensor_data()
    return [temps.get("Mount", 0.0)]

  async def get_block_target_temperature(self) -> List[float]:
    raise NotImplementedError()
  
  async def get_block_status(self) -> BlockStatus:
    raise NotImplementedError()

  # -------------------------------------------------------------------------
  # Lid Temperature
  # -------------------------------------------------------------------------

  async def set_lid_temperature(self, temperature: List[float], dynamic_time: bool = True):
    if not temperature: return
    self._lid_target_temp = temperature[0]
    block = self._block_target_temp if self._block_target_temp is not None else 25.0
    await self._run_pre_method(block, self._lid_target_temp, dynamic_time=dynamic_time)

  async def deactivate_lid(self):
    raise NotImplementedError()

  async def get_lid_current_temperature(self) -> List[float]:
    temps = await self.get_sensor_data()
    return [temps.get("Lid", 0.0)]

  async def get_lid_target_temperature(self) -> List[float]:
    raise NotImplementedError()

  # -------------------------------------------------------------------------
  # Protocol
  # -------------------------------------------------------------------------

  def _generate_method_xml(self, protocol: Protocol, block_max_volume: float, start_block_temperature: float, start_lid_temperature: float, post_heating: bool, method_name: Optional[str] = None, **kwargs) -> tuple[str, str]:

    if not method_name:
      method_name = f"PLR_Protocol_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Use ISO format with timezone for strict SiLA compliance (e.g. 2026-01-06T18:39:30.503368-08:00)
    now = datetime.datetime.now().astimezone()
    now_str = now.isoformat()

    root = ET.Element("MethodSet")
    ET.SubElement(root, "DeleteAllMethods").text = "false"

    method_elem = ET.SubElement(root, "Method", methodName=method_name, creator="PyLabRobot", dateTime=now_str)
    ET.SubElement(method_elem, "Variant").text = "960000"
    ET.SubElement(method_elem, "PlateType").text = "0"
    ET.SubElement(method_elem, "FluidQuantity").text = _format_number(block_max_volume)
    
    ET.SubElement(method_elem, "PostHeating").text = "true" if post_heating else "false"
    
    ET.SubElement(method_elem, "StartBlockTemperature").text = _format_number(start_block_temperature)
    ET.SubElement(method_elem, "StartLidTemperature").text = _format_number(start_lid_temperature)

    # Step defaults
    def_slope = _format_number(kwargs.get("slope", "4.4"))
    def_os_slope1 = _format_number(kwargs.get("overshoot_slope1", "0.1"))
    def_os_temp = _format_number(kwargs.get("overshoot_temperature", "0"))
    def_os_time = _format_number(kwargs.get("overshoot_time", "0"))
    def_os_slope2 = _format_number(kwargs.get("overshoot_slope2", "0.1"))
    pid_number = _format_number(kwargs.get("pid_number", "1"))

    step_counter = 1
    for stage_idx, stage in enumerate(protocol.stages):
      if not stage.steps: continue
      start_of_stage = step_counter
      
      for i, step in enumerate(stage.steps):
        b_temp = step.temperature[0] if step.temperature else 25
        l_temp = start_lid_temperature # Keep lid at start temp, could be extended to support step-specific lid temps
        duration = step.hold_seconds
        s_slope = _format_number(step.rate) if step.rate is not None else def_slope

        s = ET.SubElement(method_elem, "Step")
        ET.SubElement(s, "Number").text = str(step_counter)
        ET.SubElement(s, "Slope").text = s_slope
        ET.SubElement(s, "PlateauTemperature").text = _format_number(b_temp)
        ET.SubElement(s, "PlateauTime").text = _format_number(duration)
        
        # OverShoot params - use defaults passed to function
        ET.SubElement(s, "OverShootSlope1").text = def_os_slope1
        ET.SubElement(s, "OverShootTemperature").text = def_os_temp
        ET.SubElement(s, "OverShootTime").text = def_os_time
        ET.SubElement(s, "OverShootSlope2").text = def_os_slope2
        
        # Loop logic on the last step of the stage
        if i == len(stage.steps) - 1 and stage.repeats > 1:
           ET.SubElement(s, "GotoNumber").text = str(start_of_stage)
           ET.SubElement(s, "LoopNumber").text = str(stage.repeats - 1)
        else:
           ET.SubElement(s, "GotoNumber").text = "0"
           ET.SubElement(s, "LoopNumber").text = "0"

        ET.SubElement(s, "PIDNumber").text = pid_number
        ET.SubElement(s, "LidTemp").text = _format_number(l_temp)
        step_counter += 1

    # Default PID
    pid_set = ET.SubElement(method_elem, "PIDSet")
    pid = ET.SubElement(pid_set, "PID", number=pid_number)
    defaults = {"PHeating": "60", "PCooling": "80", "IHeating": "250", "ICooling": "100", 
                "DHeating": "10", "DCooling": "10", "PLid": "100", "ILid": "70"}
    for k, v in defaults.items():
        # Allow kwargs to override specific PID values, e.g. PHeating="70"
        val = kwargs.get(k, v) 
        ET.SubElement(pid, k).text = _format_number(val)

    xml_str = '<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(root, encoding="unicode")
    return xml_str, method_name

  async def run_protocol(self, protocol: Protocol, block_max_volume: float = 20.0, start_block_temperature: float = 25.0, start_lid_temperature: float = 30.0, post_heating: bool = True, method_name: Optional[str] = None, **kwargs):
    """
    Run a PCR protocol.
    
    Args:
        protocol: The protocol to run.
        block_max_volume: Fluid quantity in microliters.
        start_block_temperature: The starting block temperature in Celsius.
        start_lid_temperature: The starting lid temperature in Celsius.
        post_heating: Whether to keep heating after method end.
        method_name: Optional name for the method on the device.
        **kwargs: Additional XML parameters for the ODTC method, including:
            slope, overshoot_slope1, overshoot_temperature, overshoot_time, overshoot_slope2,
            pid_number, and PID parameters (PHeating, PCooling, etc.)
    """
    
    method_xml, method_name = self._generate_method_xml(
        protocol, 
        block_max_volume, 
        start_block_temperature, 
        start_lid_temperature, 
        post_heating, 
        method_name=method_name, 
        **kwargs
    )
    
    ps = ET.Element("ParameterSet")
    pm = ET.SubElement(ps, "Parameter", name="MethodsXML")
    ET.SubElement(pm, "String").text = method_xml
    params_xml = ET.tostring(ps, encoding="unicode")
    
    print(f"[ODTC] Uploading MethodSet...")
    await self._sila_interface.send_command("SetParameters", paramsXML=params_xml)
    
    print(f"[ODTC] Executing method '{method_name}'")
    try:
        await self._sila_interface.send_command("ExecuteMethod", methodName=method_name)
    except SiLAError as e:
        if e.code == 12: # SuccessWithWarning
            print(f"[ODTC Warning] {e.message}")
        else:
            raise e

  async def stop_method(self):
    await self._sila_interface.send_command("StopMethod")

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
