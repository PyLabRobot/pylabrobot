import time
import warnings
from enum import Enum
from typing import Dict, Literal, Optional

from pylabrobot.capabilities.shaking import ShakerBackend
from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver

from .box import HamiltonHeaterShakerInterface


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeaterShakerBackend(TemperatureControllerBackend, ShakerBackend, Driver):
  """Backend for Hamilton Heater Shaker devices."""

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    assert index >= 0, "Shaker index must be non-negative"
    self.index = index
    self.interface = interface

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def setup(self):
    await Driver.setup(self)
    await self._initialize_lock()
    await self._initialize_shaker_drive()

  async def stop(self):
    await Driver.stop(self)

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")
    return {
      **Driver.serialize(self),
      "index": self.index,
      "interface": None,
    }

  # -- shaking --

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    await self.lock_plate()

    int_speed = int(speed)
    assert 20 <= int_speed <= 2_000, "Speed must be between 20 and 2_000"
    assert direction in [0, 1], "Direction must be 0 or 1"
    assert 500 <= acceleration <= 10_000, "Acceleration must be between 500 and 10_000"

    now = time.time()
    while True:
      await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)
      if await self.get_is_shaking():
        break
      if timeout is not None and time.time() - now > timeout:
        raise TimeoutError("Failed to start shaking within timeout")

  async def stop_shaking(self):
    await self._stop_shaking()
    await self._wait_for_stop()

  async def get_is_shaking(self) -> bool:
    response = await self.interface.send_hhs_command(index=self.index, command="RD")
    return response.endswith("1")

  async def _move_plate_lock(self, position: PlateLockPosition):
    return await self.interface.send_hhs_command(index=self.index, command="LP", lp=position.value)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _initialize_lock(self):
    return await self.interface.send_hhs_command(index=self.index, command="LI")

  async def _initialize_shaker_drive(self):
    return await self.interface.send_hhs_command(index=self.index, command="SI")

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return await self.interface.send_hhs_command(
      index=self.index, command="SB", st=direction, sv=speed_str, sr=acceleration_str
    )

  async def _stop_shaking(self):
    return await self.interface.send_hhs_command(index=self.index, command="SC")

  async def _wait_for_stop(self):
    return await self.interface.send_hhs_command(index=self.index, command="SW")

  # -- temperature --

  async def set_temperature(self, temperature: float):
    assert 0 < temperature <= 105
    temp_str = f"{round(10 * temperature):04d}"
    return await self.interface.send_hhs_command(index=self.index, command="TA", ta=temp_str)

  async def _get_current_temperature(self) -> Dict[str, float]:
    response = await self.interface.send_hhs_command(index=self.index, command="RT")
    response = response.split("rt")[1]
    middle_temp = float(str(response).split(" ")[0].strip("+")) / 10
    edge_temp = float(str(response).split(" ")[1].strip("+")) / 10
    return {"middle": middle_temp, "edge": edge_temp}

  async def get_current_temperature(self) -> float:
    response = await self._get_current_temperature()
    return response["middle"]

  async def get_edge_temperature(self) -> float:
    response = await self._get_current_temperature()
    return response["edge"]

  async def deactivate(self):
    return await self.interface.send_hhs_command(index=self.index, command="TO")
