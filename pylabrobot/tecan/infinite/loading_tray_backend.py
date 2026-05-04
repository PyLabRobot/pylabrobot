from typing import Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend

from .driver import TecanInfiniteDriver


class TecanInfiniteLoadingTrayBackend(LoadingTrayBackend):
  """Loading tray backend for Tecan Infinite plate readers."""

  def __init__(self, driver: TecanInfiniteDriver):
    self._driver = driver

  async def open(self, backend_params: Optional[BackendParams] = None):
    await self._driver.send_command("ABSOLUTE MTP,OUT")
    await self._driver.send_command("BY#T5000")

  async def close(self, backend_params: Optional[BackendParams] = None):
    await self._driver.send_command("ABSOLUTE MTP,IN")
    await self._driver.send_command("BY#T5000")
