from dataclasses import dataclass
from typing import Optional

from pylabrobot.agilent.biotek.biotek import BioTekBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend


class BioTekLoadingTrayBackend(LoadingTrayBackend):
  """Loading tray backend that delegates to a BioTek serial driver."""

  @dataclass
  class OpenParams(BackendParams):
    slow: bool = False

  @dataclass
  class CloseParams(BackendParams):
    slow: bool = False

  def __init__(self, driver: BioTekBackend):
    self._driver = driver

  async def open(self, backend_params: Optional[BackendParams] = None):
    if not isinstance(backend_params, self.OpenParams):
      backend_params = self.OpenParams()
    await self._driver._set_slow_mode(backend_params.slow)
    await self._driver.send_command("J")

  async def close(self, backend_params: Optional[BackendParams] = None):
    if not isinstance(backend_params, self.CloseParams):
      backend_params = self.CloseParams()
    await self._driver._set_slow_mode(backend_params.slow)
    await self._driver.send_command("A")
