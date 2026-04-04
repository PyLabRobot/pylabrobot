import logging
import time
import warnings
from enum import Enum
from typing import Dict, Literal, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.shaking import ShakerBackend
from pylabrobot.capabilities.temperature_controlling import TemperatureControllerBackend
from pylabrobot.device import Driver

from .box import HamiltonHeaterShakerInterface

logger = logging.getLogger(__name__)


class PlateLockPosition(Enum):
  LOCKED = 1
  UNLOCKED = 0


class HamiltonHeaterShakerDriver(Driver):
  """Driver for Hamilton Heater Shaker devices.

  Owns the HamiltonHeaterShakerInterface I/O and setup/stop lifecycle.
  Device-level operations (initialize lock, initialize shaker drive) live here.
  """

  def __init__(self, index: int, interface: HamiltonHeaterShakerInterface) -> None:
    super().__init__()
    if index < 0:
      raise ValueError("Shaker index must be non-negative")
    self.index = index
    self.interface = interface

  async def setup(self, backend_params: Optional[BackendParams] = None):
    pass

  async def stop(self):
    pass

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")
    return {
      **super().serialize(),
      "index": self.index,
      "interface": None,
    }

  async def send_command(self, command: str, **kwargs) -> str:
    """Send a command to the heater shaker via the interface."""
    return await self.interface.send_hhs_command(index=self.index, command=command, **kwargs)


class HamiltonHeaterShakerShakerBackend(ShakerBackend):
  """Translates ShakerBackend interface into Hamilton Heater Shaker driver commands."""

  def __init__(self, driver: HamiltonHeaterShakerDriver) -> None:
    self.driver = driver

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await self.driver.send_command("SI")

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

    logger.info("[HHS %d] start shaking: speed=%d rpm, direction=%d, acceleration=%d", self.driver.index, int_speed, direction, acceleration)
    now = time.time()
    while True:
      await self._start_shaking(direction=direction, speed=int_speed, acceleration=acceleration)
      if await self.request_is_shaking():
        break
      if timeout is not None and time.time() - now > timeout:
        logger.error("[HHS %d] failed to start shaking within %ss timeout", self.driver.index, timeout)
        raise TimeoutError("Failed to start shaking within timeout")

  async def stop_shaking(self):
    logger.info("[HHS %d] stop shaking", self.driver.index)
    await self._stop_shaking()
    await self._wait_for_stop()

  async def request_is_shaking(self) -> bool:
    response = await self.driver.send_command("RD")
    is_shaking = response.endswith("1")
    logger.debug("[HHS %d] read shaking status: is_shaking=%s", self.driver.index, is_shaking)
    return is_shaking

  async def _move_plate_lock(self, position: PlateLockPosition):
    return await self.driver.send_command("LP", lp=position.value)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._move_plate_lock(PlateLockPosition.LOCKED)

  async def unlock_plate(self):
    await self._move_plate_lock(PlateLockPosition.UNLOCKED)

  async def _start_shaking(self, direction: int, speed: int, acceleration: int):
    speed_str = str(speed).zfill(4)
    acceleration_str = str(acceleration).zfill(5)
    return await self.driver.send_command("SB", st=direction, sv=speed_str, sr=acceleration_str)

  async def _stop_shaking(self):
    return await self.driver.send_command("SC")

  async def _wait_for_stop(self):
    return await self.driver.send_command("SW")


class HamiltonHeaterShakerTemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend interface into Hamilton Heater Shaker driver
  commands."""

  def __init__(self, driver: HamiltonHeaterShakerDriver) -> None:
    self.driver = driver

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    await self.driver.send_command("LI")

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    if not (0 < temperature <= 105):
      raise ValueError(f"Temperature must be between 0 (exclusive) and 105, got {temperature}")
    logger.info("[HHS %d] set temperature: target=%.1f C", self.driver.index, temperature)
    temp_str = f"{round(10 * temperature):04d}"
    return await self.driver.send_command("TA", ta=temp_str)

  async def _request_current_temperature(self) -> Dict[str, float]:
    response = await self.driver.send_command("RT")
    response = response.split("rt")[1]
    middle_temp = float(str(response).split(" ")[0].strip("+")) / 10
    edge_temp = float(str(response).split(" ")[1].strip("+")) / 10
    return {"middle": middle_temp, "edge": edge_temp}

  async def request_current_temperature(self) -> float:
    response = await self._request_current_temperature()
    temp = response["middle"]
    logger.info("[HHS %d] read temperature: actual=%.1f C", self.driver.index, temp)
    return temp

  async def request_edge_temperature(self) -> float:
    response = await self._request_current_temperature()
    temp = response["edge"]
    logger.info("[HHS %d] read edge temperature: actual=%.1f C", self.driver.index, temp)
    return temp

  async def deactivate(self):
    logger.info("[HHS %d] deactivate temperature control", self.driver.index)
    return await self.driver.send_command("TO")
