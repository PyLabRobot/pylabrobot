from dataclasses import dataclass
from typing import Optional

from pylabrobot.agilent.biotek.plate_readers.base import BioTekBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource


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
    await self._driver.set_slow_mode(backend_params.slow)
    await self._driver.send_command("J")

  async def close(
    self,
    backend_params: Optional[BackendParams] = None,
    resource: Optional[Resource] = None,
  ):
    if not isinstance(backend_params, self.CloseParams):
      backend_params = self.CloseParams()
    # Closing invalidates whatever plate geometry the firmware last had loaded.
    self._driver.clear_plate()
    await self._driver.set_slow_mode(backend_params.slow)
    # Send the plate geometry to the firmware before closing so the carrier accounts for the
    # labware height during the close motion. Without this, a tall plate can jam the tray.
    if isinstance(resource, Plate):
      await self._driver.set_plate(resource)
    await self._driver.send_command("A")
