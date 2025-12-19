import xml.etree.ElementTree as ET
from typing import Any, Dict, Literal, Optional

from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface


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
  return s  # String or unknown => raw text


def _get_param(root: ET.Element, name: str):
  p = root.find(f".//Parameter[@name='{name}']")
  if p is None or len(p) == 0:
    raise RuntimeError(f"Response missing parameter '{name}'")
  child = next(iter(p))  # e.g. <Float64>, <Boolean>, <String>
  return _parse_scalar(child.text, child.tag)


def _get_params(root: ET.Element, names: list[str]) -> dict[str, object]:
  return {n: _get_param(root, n) for n in names}


class SCILABackend:
  def __init__(self, client_ip: str, scila_ip: str) -> None:
    self._sila_interface = InhecoSiLAInterface(client_ip=client_ip, scila_ip=scila_ip)

  async def setup(self) -> None:
    await self._sila_interface.setup()
    await self._reset_and_initialize()

  async def _reset_and_initialize(self) -> None:
    event_uri = f"http://{self._sila_interface._client_ip}:{self._sila_interface.bound_port}/"
    await self._sila_interface.send_command(
      command="Reset", deviceId="MyController", eventReceiverURI=event_uri, simulationMode=False
    )

    await self._sila_interface.send_command("Initialize")

  async def get_status(self) -> str:
    # "standBy", "inError", "startup"
    resp = await self._sila_interface.send_command("GetStatus")
    return resp.get("GetStatusResponse", {}).get("state", "Unknown")

  async def get_liquid_level(self) -> str:
    root = await self._sila_interface.send_command("GetLiquidLevel")
    return _get_param(root, "LiquidLevel")  # type: ignore

  async def get_temperature_information(self) -> Dict[str, Any]:
    root = await self._sila_interface.send_command("GetTemperature")
    return _get_params(root, ["CurrentTemperature", "TargetTemperature", "TemperatureControl"])  # type: ignore

  async def get_current_temperature(self) -> float:
    return (await self.get_temperature_information())["CurrentTemperature"]  # type: ignore

  async def get_target_temperature(self) -> float:
    return (await self.get_temperature_information())["TargetTemperature"]  # type: ignore

  async def get_temperature_control_enabled(self) -> bool:
    return (await self.get_temperature_information())["TemperatureControl"]  # type: ignore

  async def open_drawer(self, drawer_id: int) -> None:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    await self._sila_interface.send_command("PrepareForInput", position=drawer_id)
    await self._sila_interface.send_command("OpenDoor")

  async def close_drawer(self, drawer_id: int) -> None:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    await self._sila_interface.send_command("PrepareForOutput", position=drawer_id)
    await self._sila_interface.send_command("CloseDoor")

  DrawerPosition = Literal["Opened", "Closed"]

  async def get_drawer_positions(self) -> Dict[str, DrawerPosition]:
    root = await self._sila_interface.send_command("GetDoorStatus")
    return _get_params(root, ["Drawer1", "Drawer2", "Drawer3", "Drawer4"])  # type: ignore

  async def get_drawer_position(self, drawer_id: int) -> DrawerPosition:
    if not drawer_id in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    positions = await self.get_drawer_positions()
    return positions[f"Drawer{drawer_id}"]

  async def get_co2_flow_status(self) -> str:
    # "NOK", ...?
    root = await self._sila_interface.send_command("GetCO2FlowStatus")
    return _get_param(root, "CO2FlowStatus")  # type: ignore

  async def get_valve_status(self) -> Dict[str, str]:
    """
    example:

    {
      "H2O": "Opened",
      "CO2 Normal": "Opened",
      "CO2 Boost": "Closed"
    }
    """

    root = await self._sila_interface.send_command("GetValveStatus")
    return _get_params(root, ["H2O", "CO2 Normal", "CO2 Boost"])  # type: ignore

  async def set_tempeature(self, temperature: float) -> None:
    await self._sila_interface.send_command(
      "SetTemperature", targetTemperature=temperature, temperatureControl=True
    )

  async def deactivate_temperature_control(self) -> None:
    await self._sila_interface.send_command("SetTemperature", temperatureControl=False)
