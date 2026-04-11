"""Legacy. Use pylabrobot.inheco.scila.SCILADriver and SCILATemperatureBackend instead."""

from typing import Any, Dict, Literal, Optional

from pylabrobot.inheco.scila.scila_backend import SCILADriver, SCILATemperatureBackend
from pylabrobot.legacy.machines.backend import MachineBackend

DrawerStatus = Literal["Opened", "Closed"]


class SCILABackend(MachineBackend):
  """Legacy. Use pylabrobot.inheco.scila.SCILADriver and SCILATemperatureBackend instead."""

  def __init__(self, scila_ip: str, client_ip: Optional[str] = None) -> None:
    self.driver = SCILADriver(scila_ip=scila_ip, client_ip=client_ip)
    self._temp = SCILATemperatureBackend(driver=self.driver)

  @property
  def _sila_interface(self):
    return self.driver._sila_interface

  async def setup(self) -> None:
    await self.driver.setup()
    await self._temp._on_setup()

  async def stop(self) -> None:
    await self._temp._on_stop()
    await self.driver.stop()

  async def request_status(self) -> str:
    return await self.driver.request_status()

  async def request_liquid_level(self) -> str:
    return await self.driver.request_liquid_level()

  async def request_temperature_information(self) -> dict[str, Any]:
    return await self._temp.request_temperature_information()

  async def measure_temperature(self) -> float:
    return await self._temp.request_current_temperature()

  async def request_target_temperature(self) -> float:
    return await self._temp.request_target_temperature()

  async def is_temperature_control_enabled(self) -> bool:
    return await self._temp.is_temperature_control_enabled()

  async def open(self, drawer_id: int) -> None:
    await self.driver.open(drawer_id=drawer_id)

  async def close(self, drawer_id: int) -> None:
    await self.driver.close(drawer_id=drawer_id)

  async def request_drawer_statuses(self) -> Dict[int, DrawerStatus]:
    return await self.driver.request_drawer_statuses()

  async def request_drawer_status(self, drawer_id: int) -> DrawerStatus:
    return await self.driver.request_drawer_status(drawer_id=drawer_id)

  async def request_co2_flow_status(self) -> str:
    return await self.driver.request_co2_flow_status()

  async def request_valve_status(self) -> dict[str, str]:
    return await self.driver.request_valve_status()

  async def start_temperature_control(self, temperature: float) -> None:
    await self._temp.set_temperature(temperature=temperature)

  async def stop_temperature_control(self) -> None:
    await self._temp.deactivate()

  def serialize(self) -> dict[str, Any]:
    return self.driver.serialize()

  @classmethod
  def deserialize(cls, data: dict[str, Any]) -> "SCILABackend":
    return cls(scila_ip=data["scila_ip"], client_ip=data.get("client_ip"))
