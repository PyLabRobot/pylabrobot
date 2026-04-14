import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, Literal, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver

from .inheco_sila_interface import InhecoSiLAInterface

logger = logging.getLogger(__name__)


def _parse_scalar(text: Optional[str], tag: str) -> object:
  if text is None:
    return None
  s = text.strip()
  t = tag.lower()
  if t in ("float64", "float32", "double", "single"):
    return float(s)
  if t in ("int32", "int64", "uint32", "uint64", "integer"):
    return int(s)
  if t in ("boolean", "bool"):
    return s.lower() == "true"
  return s


def _get_param(root: ET.Element, name: str):
  p = root.find(f".//Parameter[@name='{name}']")
  if p is None or len(p) == 0:
    raise RuntimeError(f"Response missing parameter '{name}'")
  child = next(iter(p))
  return _parse_scalar(child.text, child.tag)


def _get_params(root: ET.Element, names: list[str]) -> dict[str, object]:
  return {n: _get_param(root, n) for n in names}


DrawerStatus = Literal["Opened", "Closed"]


class SCILADriver(Driver):
  """Hardware driver for Inheco SCILA incubators.

  Owns the SiLA HTTP/SOAP connection and exposes generic send_command(),
  plus device-level operations (drawers, status, CO2/valves).
  """

  def __init__(self, scila_ip: str, client_ip: Optional[str] = None) -> None:
    super().__init__()
    self._sila_interface = InhecoSiLAInterface(client_ip=client_ip, machine_ip=scila_ip)

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    await self._sila_interface.setup()
    await self._reset_and_initialize()
    logger.info("[SCILA %s] connected", self._sila_interface.machine_ip)

  async def stop(self) -> None:
    await self._sila_interface.close()
    logger.info("[SCILA %s] connection closed", self._sila_interface.machine_ip)

  async def send_command(self, command: str, **kwargs) -> Any:
    """Send a SiLA command and return the parsed response."""
    return await self._sila_interface.send_command(command, **kwargs)

  async def _reset_and_initialize(self) -> None:
    event_uri = f"http://{self._sila_interface.client_ip}:{self._sila_interface.bound_port}/"
    await self.send_command(
      command="Reset", deviceId="MyController", eventReceiverURI=event_uri, simulationMode=False
    )
    await self.send_command("Initialize")

  # -- status queries --

  async def request_status(self) -> str:
    resp = await self.send_command("GetStatus")
    return resp.get("GetStatusResponse", {}).get("state", "Unknown")  # type: ignore

  async def request_liquid_level(self) -> str:
    root = await self.send_command("GetLiquidLevel")
    return _get_param(root, "LiquidLevel")  # type: ignore

  # -- drawers --

  async def open(self, drawer_id: int) -> None:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    logger.info("[SCILA %s] open drawer: drawer_id=%d", self._sila_interface.machine_ip, drawer_id)
    await self.send_command("PrepareForInput", position=drawer_id)
    await self.send_command("OpenDoor")

  async def close(self, drawer_id: int) -> None:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    logger.info("[SCILA %s] close drawer: drawer_id=%d", self._sila_interface.machine_ip, drawer_id)
    await self.send_command("PrepareForOutput", position=drawer_id)
    await self.send_command("CloseDoor")

  async def request_drawer_statuses(self) -> Dict[int, DrawerStatus]:
    root = await self.send_command("GetDoorStatus")
    params = _get_params(root, ["Drawer1", "Drawer2", "Drawer3", "Drawer4"])
    return {i: params[f"Drawer{i}"] for i in range(1, 5)}  # type: ignore

  async def request_drawer_status(self, drawer_id: int) -> DrawerStatus:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    positions = await self.request_drawer_statuses()
    return positions[drawer_id]

  # -- CO2 / valves --

  async def request_co2_flow_status(self) -> str:
    root = await self.send_command("GetCO2FlowStatus")
    return _get_param(root, "CO2FlowStatus")  # type: ignore

  async def request_valve_status(self) -> dict[str, str]:
    root = await self.send_command("GetValveStatus")
    return _get_params(root, ["H2O", "CO2 Normal", "CO2 Boost"])  # type: ignore

  # -- serialization --

  def serialize(self) -> dict[str, Any]:
    return {
      **super().serialize(),
      "scila_ip": self._sila_interface.machine_ip,
      "client_ip": self._sila_interface.client_ip,
    }


class SCILATemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend interface into SCILA SiLA commands."""

  def __init__(self, driver: SCILADriver) -> None:
    self.driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def request_temperature_information(self) -> dict[str, Any]:
    root = await self.driver.send_command("GetTemperature")
    return _get_params(root, ["CurrentTemperature", "TargetTemperature", "TemperatureControl"])  # type: ignore

  async def set_temperature(self, temperature: float) -> None:
    logger.info(
      "[SCILA %s] set temperature: target=%.1f C",
      self.driver._sila_interface.machine_ip,
      temperature,
    )
    await self.driver.send_command(
      "SetTemperature", targetTemperature=temperature, temperatureControl=True
    )

  async def request_current_temperature(self) -> float:
    temp: float = (await self.request_temperature_information())["CurrentTemperature"]  # type: ignore[index]
    logger.info(
      "[SCILA %s] read temperature: actual=%.1f C", self.driver._sila_interface.machine_ip, temp
    )
    return temp

  async def deactivate(self) -> None:
    logger.info("[SCILA %s] deactivate temperature control", self.driver._sila_interface.machine_ip)
    await self.driver.send_command("SetTemperature", temperatureControl=False)

  async def request_target_temperature(self) -> float:
    return (await self.request_temperature_information())["TargetTemperature"]  # type: ignore

  async def is_temperature_control_enabled(self) -> bool:
    return (await self.request_temperature_information())["TemperatureControl"]  # type: ignore
