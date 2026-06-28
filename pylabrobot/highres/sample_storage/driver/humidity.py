from typing import TYPE_CHECKING

from pylabrobot.capabilities.humidity_controlling.backend import HumidityControllerBackend

from ..errors import HighResSampleStorageError

if TYPE_CHECKING:
  from .driver import HighResSampleStorageDriver


class HighResSampleStorageHumidityControllerBackend(HumidityControllerBackend):
  """Humidity monitoring for a HighRes sample store (read-only; no active control)."""

  def __init__(self, driver: "HighResSampleStorageDriver"):
    super().__init__()
    self._driver = driver

  @property
  def supports_humidity_control(self) -> bool:
    return False

  async def request_current_humidity(self) -> float:
    env = await self._driver.request_environment()
    if "RH" not in env:
      raise HighResSampleStorageError("environmentstatus", ["no RH channel reported"])
    return env["RH"].current / 100.0

  async def set_humidity(self, humidity: float):
    raise NotImplementedError("HighRes sample stores do not support active humidity control.")
