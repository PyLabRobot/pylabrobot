"""Legacy. Use pylabrobot.inheco.odtc instead."""

from typing import Dict, List, Optional

from pylabrobot.inheco.odtc.odtc import ODTCBlockBackend, ODTCDriver, ODTCThermocyclingBackend
from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
  protocol_to_new,
)


class ExperimentalODTCBackend(ThermocyclerBackend):
  """Legacy. Use pylabrobot.inheco.odtc.ODTC instead."""

  def __init__(self, ip: str, client_ip: Optional[str] = None) -> None:
    self._driver = ODTCDriver(ip=ip, client_ip=client_ip)
    self._tc = ODTCThermocyclingBackend(self._driver)
    self._block_target_temp: Optional[float] = None
    self._lid_target_temp: Optional[float] = None
    self._sensor_cache: Dict = {}

  @property
  def _sila_interface(self):
    return self._driver._sila

  async def setup(self) -> None:
    await self._driver.setup()

  async def stop(self):
    await self._driver.stop()

  async def open_lid(self):
    await self._tc.open_lid()

  async def close_lid(self):
    await self._tc.close_lid()

  async def get_lid_open(self) -> bool:
    raise NotImplementedError()

  async def get_lid_status(self) -> LidStatus:
    raise NotImplementedError()

  async def get_sensor_data(self) -> Dict[str, float]:
    return await self._driver.get_sensor_data(self._sensor_cache)

  async def set_block_temperature(self, temperature: List[float], dynamic_time: bool = True):
    if not temperature:
      return
    self._block_target_temp = temperature[0]
    lid = self._lid_target_temp if self._lid_target_temp is not None else 105.0
    block_be = ODTCBlockBackend(self._driver)
    block_be._lid_target = lid
    await block_be._run_pre_method(self._block_target_temp, lid)

  async def deactivate_block(self):
    await self._driver.send_command("StopMethod")

  async def get_block_current_temperature(self) -> List[float]:
    data = await self._driver.get_sensor_data(self._sensor_cache)
    return [data.get("Mount", 0.0)]

  async def get_block_target_temperature(self) -> List[float]:
    raise NotImplementedError()

  async def get_block_status(self) -> BlockStatus:
    raise NotImplementedError()

  async def set_lid_temperature(self, temperature: List[float], dynamic_time: bool = True):
    if not temperature:
      return
    self._lid_target_temp = temperature[0]
    block = self._block_target_temp if self._block_target_temp is not None else 25.0
    block_be = ODTCBlockBackend(self._driver)
    block_be._lid_target = self._lid_target_temp
    await block_be._run_pre_method(block, self._lid_target_temp)

  async def deactivate_lid(self):
    raise NotImplementedError()

  async def get_lid_current_temperature(self) -> List[float]:
    data = await self._driver.get_sensor_data(self._sensor_cache)
    return [data.get("Lid", 0.0)]

  async def get_lid_target_temperature(self) -> List[float]:
    raise NotImplementedError()

  async def run_protocol(
    self,
    protocol: Protocol,
    block_max_volume: float = 20.0,
    start_block_temperature: float = 25.0,
    start_lid_temperature: float = 30.0,
    post_heating: bool = True,
    method_name: Optional[str] = None,
    **kwargs,
  ):
    new_protocol = protocol_to_new(protocol)
    await self._tc.run_protocol(
      protocol=new_protocol,
      block_max_volume=block_max_volume,
      start_block_temperature=start_block_temperature,
      start_lid_temperature=start_lid_temperature,
      post_heating=post_heating,
      method_name=method_name,
      **kwargs,
    )

  async def stop_method(self):
    await self._driver.send_command("StopMethod")

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
