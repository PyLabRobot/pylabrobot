from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.loading_tray.backend import LoadingTrayBackend

from .backend import MolecularDevicesDriver

if TYPE_CHECKING:
  from pylabrobot.resources.resource import Resource


class MolecularDevicesLoadingTrayBackend(LoadingTrayBackend):
  """Loading tray backend for Molecular Devices plate readers."""

  def __init__(self, driver: MolecularDevicesDriver):
    self._driver = driver

  async def open(self, backend_params: Optional[BackendParams] = None):
    await self._driver.send_command("!OPEN")

  async def close(
    self,
    backend_params: Optional[BackendParams] = None,
    plate: Optional["Resource"] = None,
  ):
    await self._driver.send_command("!CLOSE")
