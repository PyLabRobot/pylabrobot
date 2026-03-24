"""Legacy. Use pylabrobot.opentrons.thermocycler instead."""

from typing import List

from pylabrobot.legacy.thermocycling.backend import ThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
  protocol_to_new,
)
from pylabrobot.opentrons.thermocycler import (
  OpentronsThermocyclerDriver,
  OpentronsThermocyclingBackend,
)


class OpentronsThermocyclerBackend(ThermocyclerBackend):
  """Legacy. Use pylabrobot.opentrons.OpentronsThermocyclingBackend instead."""

  def __init__(self, opentrons_id: str):
    self._driver = OpentronsThermocyclerDriver(opentrons_id=opentrons_id)
    self._new = OpentronsThermocyclingBackend(self._driver)

  @property
  def opentrons_id(self):
    return self._driver.opentrons_id

  async def setup(self):
    await self._new.setup()

  async def stop(self):
    await self._new.stop()

  def serialize(self) -> dict:
    return {"type": self.__class__.__name__, "opentrons_id": self.opentrons_id}

  async def open_lid(self):
    await self._new.open_lid()

  async def close_lid(self):
    await self._new.close_lid()

  async def set_block_temperature(self, temperature: List[float]):
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique block temperature, "
        f"got {set(temperature)}"
      )
    self._driver.set_block_temperature(temperature[0])

  async def set_lid_temperature(self, temperature: List[float]):
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique lid temperature, "
        f"got {set(temperature)}"
      )
    self._driver.set_lid_temperature(temperature[0])

  async def deactivate_block(self):
    self._driver.deactivate_block()

  async def deactivate_lid(self):
    self._driver.deactivate_lid()

  async def run_protocol(self, protocol: Protocol, block_max_volume: float):
    await self._new.run_protocol(protocol_to_new(protocol), block_max_volume)

  async def get_block_current_temperature(self) -> List[float]:
    return [self._driver.get_block_current_temperature()]

  async def get_block_target_temperature(self) -> List[float]:
    target = self._driver.get_block_target_temperature()
    if target is None:
      raise RuntimeError("Block target temperature is not set. Is a cycle running?")
    return [target]

  async def get_lid_current_temperature(self) -> List[float]:
    return [self._driver.get_lid_current_temperature()]

  async def get_lid_target_temperature(self) -> List[float]:
    target = self._driver.get_lid_target_temperature()
    if target is None:
      raise RuntimeError("Lid target temperature is not set. Is a cycle running?")
    return [target]

  async def get_lid_open(self) -> bool:
    return await self._new.get_lid_open()

  async def get_lid_status(self) -> LidStatus:
    status = self._driver.get_lid_temperature_status_str()
    if status == "holding at target":
      return LidStatus.HOLDING_AT_TARGET
    return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    status = self._driver.get_block_status_str()
    if status == "holding at target":
      return BlockStatus.HOLDING_AT_TARGET
    return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    return await self._new.get_hold_time()

  async def get_current_cycle_index(self) -> int:
    return await self._new.get_current_cycle_index()

  async def get_total_cycle_count(self) -> int:
    return await self._new.get_total_cycle_count()

  async def get_current_step_index(self) -> int:
    return await self._new.get_current_step_index()

  async def get_total_step_count(self) -> int:
    return await self._new.get_total_step_count()
