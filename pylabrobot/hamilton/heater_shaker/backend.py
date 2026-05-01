import asyncio
import logging
import time
from enum import Enum
from typing import Dict, Literal, Optional, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.shaking import ShakerBackend
from pylabrobot.capabilities.shaking.backend import HasContinuousShaking
from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.hamilton.usb.driver import HamiltonUSBDriver

logger = logging.getLogger(__name__)


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeaterShakerBackend(
  ShakerBackend, HasContinuousShaking, TemperatureControllerBackend
):
  """Backend for Hamilton Heater Shaker: combined shaking and temperature control."""

  def __init__(self, driver: HamiltonUSBDriver, index: int) -> None:
    self.driver = driver
    self.index = index

  async def _send_command(self, command: str, **kwargs) -> str:
    resp = await self.driver.send_command(module=f"T{self.index}", command=command, **kwargs)
    return cast(str, resp)

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await self._send_command("SI")
    await self._send_command("LI")

  # -- shaking --

  async def shake(self, speed: float, duration: float, backend_params=None):
    await self.start_shaking(speed=speed)
    await asyncio.sleep(duration)
    await self.stop_shaking()

  async def start_shaking(
    self,
    speed: float = 800,
    direction: Literal[0, 1] = 0,
    acceleration: int = 1_000,
    timeout: Optional[float] = 30,
  ):
    await self.lock_plate()

    int_speed = int(speed)
    if not (20 <= int_speed <= 2_000):
      raise ValueError("Speed must be between 20 and 2_000")
    if direction not in [0, 1]:
      raise ValueError("Direction must be 0 or 1")
    if not (500 <= acceleration <= 10_000):
      raise ValueError("Acceleration must be between 500 and 10_000")

    logger.info(
      "[HHS %d] start shaking: speed=%d rpm, direction=%d, acceleration=%d",
      self.index,
      int_speed,
      direction,
      acceleration,
    )
    now = time.time()
    while True:
      speed_str = str(int_speed).zfill(4)
      acceleration_str = str(acceleration).zfill(5)
      await self._send_command("SB", st=direction, sv=speed_str, sr=acceleration_str)
      if await self.request_is_shaking():
        break
      if timeout is not None and time.time() - now > timeout:
        logger.error("[HHS %d] failed to start shaking within %ss timeout", self.index, timeout)
        raise TimeoutError("Failed to start shaking within timeout")

  async def stop_shaking(self):
    logger.info("[HHS %d] stop shaking", self.index)
    await self._send_command("SC")
    await self._send_command("SW")

  async def request_is_shaking(self) -> bool:
    response = await self._send_command("RD")
    is_shaking = response.endswith("1")
    logger.debug("[HHS %d] read shaking status: is_shaking=%s", self.index, is_shaking)
    return is_shaking

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._send_command("LP", lp=PlateLockPosition.LOCKED.value)

  async def unlock_plate(self):
    await self._send_command("LP", lp=PlateLockPosition.UNLOCKED.value)

  # -- temperature --

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    if not (0 < temperature <= 105):
      raise ValueError(f"Temperature must be between 0 (exclusive) and 105, got {temperature}")
    logger.info("[HHS %d] set temperature: target=%.1f C", self.index, temperature)
    temp_str = f"{round(10 * temperature):04d}"
    await self._send_command("TA", ta=temp_str)

  async def _request_current_temperature(self) -> Dict[str, float]:
    response = await self._send_command("RT")
    response = response.split("rt")[1]
    middle_temp = float(str(response).split(" ")[0].strip("+")) / 10
    edge_temp = float(str(response).split(" ")[1].strip("+")) / 10
    return {"middle": middle_temp, "edge": edge_temp}

  async def request_current_temperature(self) -> float:
    response = await self._request_current_temperature()
    temp = response["middle"]
    logger.info("[HHS %d] read temperature: actual=%.1f C", self.index, temp)
    return temp

  async def request_edge_temperature(self) -> float:
    response = await self._request_current_temperature()
    temp = response["edge"]
    logger.info("[HHS %d] read edge temperature: actual=%.1f C", self.index, temp)
    return temp

  async def deactivate(self):
    logger.info("[HHS %d] deactivate temperature control", self.index)
    await self._send_command("TO")
