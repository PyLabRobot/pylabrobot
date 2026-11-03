import logging
from typing import Any, Dict, Literal, Optional

from pylabrobot.inheco.transport.sila import InhecoSiLAInterface, get_params
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.resource_holder import ResourceHolder

logger = logging.getLogger(__name__)


DrawerStatus = Literal["Opened", "Closed"]


class SCILADrawerLoadingTray(ResourceHolder):
  """One of the SCILA's four drawers: a loading tray that opens and closes."""

  def __init__(
    self,
    scila: "SCILA",
    drawer_id: int,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    child_location: Coordinate = Coordinate.zero(),
    category: str = "loading_tray",
    model: Optional[str] = None,
  ):
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    ResourceHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      category=category,
      model=model,
    )
    self._scila = scila
    self._drawer_id = drawer_id

  async def open(self):
    await self._scila.send_command("PrepareForInput", position=self._drawer_id)
    try:
      await self._scila.send_command("OpenDoor")
    except RuntimeError as e:
      self._handle_warning("open", e)

  async def close(self):
    await self._scila.send_command("PrepareForOutput", position=self._drawer_id)
    try:
      await self._scila.send_command("CloseDoor")
    except RuntimeError as e:
      self._handle_warning("close", e)

  def _handle_warning(self, action: str, e: RuntimeError) -> None:
    if "warning" not in str(e).lower():
      raise e
    # SCILA emits a non-fatal CO2-flow warning when the gas mixer is absent or unhealthy.
    # When the user has declared no gas mixer is connected, silence it; otherwise surface
    # the warning so a real CO2 problem isn't hidden.
    if self._scila.gas_mixer_connected:
      logger.warning("drawer %d %s: %s", self._drawer_id, action, e)


class SCILA(Resource):
  """Inheco SCILA incubator: four drawers and temperature control, over SiLA.

  Owns the SiLA HTTP/SOAP connection and exposes the device's operations directly.

  Example:
    >>> from pylabrobot.inheco.scila import SCILA
    >>> scila = SCILA(name="scila", scila_ip="169.254.1.117")
    >>> await scila.setup()
    >>> await scila.set_temperature(37.0)
    >>> await scila.drawers[2].open()
  """

  def __init__(
    self,
    name: str,
    scila_ip: str,
    client_ip: Optional[str] = None,
    gas_mixer_connected: bool = True,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Inheco SCILA",
    )
    self._sila_interface = InhecoSiLAInterface(client_ip=client_ip, machine_ip=scila_ip)
    self.gas_mixer_connected = gas_mixer_connected

    self.drawers: Dict[int, SCILADrawerLoadingTray] = {}
    for drawer_id in range(1, 5):
      tray = SCILADrawerLoadingTray(
        scila=self,
        drawer_id=drawer_id,
        name=f"{name}_drawer_{drawer_id}",
        size_x=0.0,  # TODO: measure
        size_y=0.0,  # TODO: measure
        size_z=0.0,  # TODO: measure
        child_location=Coordinate.zero(),  # TODO: measure
      )
      self.drawers[drawer_id] = tray
      self.assign_child_resource(tray, location=Coordinate.zero())  # TODO: measure

  # -- lifecycle --

  async def setup(self) -> None:
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
    return get_params(root, ["LiquidLevel"])["LiquidLevel"]  # type: ignore

  # -- drawers --

  async def request_drawer_statuses(self) -> Dict[int, DrawerStatus]:
    root = await self.send_command("GetDoorStatus")
    params = get_params(root, ["Drawer1", "Drawer2", "Drawer3", "Drawer4"])
    return {i: params[f"Drawer{i}"] for i in range(1, 5)}  # type: ignore

  async def request_drawer_status(self, drawer_id: int) -> DrawerStatus:
    if drawer_id not in {1, 2, 3, 4}:
      raise ValueError(f"Invalid drawer ID: {drawer_id}. Must be 1, 2, 3, or 4.")
    positions = await self.request_drawer_statuses()
    return positions[drawer_id]

  # -- CO2 / valves --

  async def request_co2_flow_status(self) -> str:
    root = await self.send_command("GetCO2FlowStatus")
    return get_params(root, ["CO2FlowStatus"])["CO2FlowStatus"]  # type: ignore

  async def request_valve_status(self) -> dict[str, str]:
    root = await self.send_command("GetValveStatus")
    return get_params(root, ["H2O", "CO2 Normal", "CO2 Boost"])  # type: ignore

  # -- temperature control --

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def request_temperature_information(self) -> dict[str, Any]:
    root = await self.send_command("GetTemperature")
    return get_params(root, ["CurrentTemperature", "TargetTemperature", "TemperatureControl"])  # type: ignore

  async def set_temperature(self, temperature: float) -> None:
    logger.info(
      "[SCILA %s] set temperature: target=%.1f C",
      self._sila_interface.machine_ip,
      temperature,
    )
    await self.send_command(
      "SetTemperature", targetTemperature=temperature, temperatureControl=True
    )

  async def request_current_temperature(self) -> float:
    temp: float = (await self.request_temperature_information())["CurrentTemperature"]  # type: ignore[index]
    logger.info("[SCILA %s] read temperature: actual=%.1f C", self._sila_interface.machine_ip, temp)
    return temp

  async def deactivate(self) -> None:
    logger.info("[SCILA %s] deactivate temperature control", self._sila_interface.machine_ip)
    await self.send_command("SetTemperature", temperatureControl=False)

  async def request_target_temperature(self) -> float:
    return (await self.request_temperature_information())["TargetTemperature"]  # type: ignore

  async def is_temperature_control_enabled(self) -> bool:
    return (await self.request_temperature_information())["TemperatureControl"]  # type: ignore

  # -- serialization --

  def serialize(self) -> dict[str, Any]:
    return {
      **super().serialize(),
      "scila_ip": self._sila_interface.machine_ip,
      "client_ip": self._sila_interface.client_ip,
      "gas_mixer_connected": self.gas_mixer_connected,
    }
