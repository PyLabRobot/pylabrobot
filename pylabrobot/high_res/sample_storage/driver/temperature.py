from typing import TYPE_CHECKING

from pylabrobot.capabilities.temperature_controlling.backend import TemperatureControllerBackend

from ..errors import HighResSampleStorageError

if TYPE_CHECKING:
  from .driver import HighResSampleStorageDriver


class HighResSampleStorageTemperatureControllerBackend(TemperatureControllerBackend):
  """Temperature control for a HighRes sample store (refrigerated, -20 to 4 C)."""

  def __init__(self, driver: "HighResSampleStorageDriver"):
    super().__init__()
    self._driver = driver

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def request_current_temperature(self) -> float:
    env = await self._driver.request_environment()
    if "TEMP" not in env:
      raise HighResSampleStorageError("environmentstatus", ["no TEMP channel reported"])
    return env["TEMP"].current

  async def set_temperature(self, temperature: float):
    await self._driver.send_command(f"environmentset TEMP {temperature}")

  async def deactivate(self):
    await self._driver.send_command("environment TEMP off")
