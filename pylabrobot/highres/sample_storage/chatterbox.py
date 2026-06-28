import logging
from typing import Dict, List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver

from .driver import (
  HighResSampleStorageAutomatedRetrievalBackend,
  HighResSampleStorageDriver,
  HighResSampleStorageHumidityControllerBackend,
  HighResSampleStorageTemperatureControllerBackend,
)
from .types import EnvironmentParameter, VersionInfo

logger = logging.getLogger(__name__)


class HighResSampleStorageChatterboxDriver(HighResSampleStorageDriver):
  """Device-free driver that logs commands instead of talking to hardware.

  Owns the real per-capability backends, so it exercises their command-building
  logic; only the transport (:meth:`send_command`) and the shared device queries
  are faked. Useful for testing protocols and resource assignment offline.
  """

  def __init__(
    self,
    temperature: float = 4.0,
    humidity: float = 0.5,
    loading_tray_nest: int = 1,
    num_nests: int = 2,
  ):
    Driver.__init__(self)
    self.io = None  # type: ignore[assignment]
    self._read_timeout = 30.0
    self._motion_timeout = 240.0
    self._temperature = temperature
    self._humidity = humidity

    self.automated_retrieval = HighResSampleStorageAutomatedRetrievalBackend(
      self, loading_tray_nest=loading_tray_nest, num_nests=num_nests
    )
    self.temperature = HighResSampleStorageTemperatureControllerBackend(self)
    self.humidity = HighResSampleStorageHumidityControllerBackend(self)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    logger.info("[chatterbox] setup")

  async def stop(self):
    logger.info("[chatterbox] stop")

  async def send_command(self, command: str, timeout: Optional[float] = None) -> List[str]:
    logger.info("[chatterbox] %s", command)
    return []

  async def request_version(self) -> VersionInfo:
    return VersionInfo(
      product_name="HighRes sample store",
      serial_number="CHATTERBOX",
      firmware_version="0.0.0",
      firmware_build=None,
      raw={},
    )

  async def request_environment(self) -> Dict[str, EnvironmentParameter]:
    return {
      "TEMP": EnvironmentParameter(name="TEMP", current=self._temperature),
      "RH": EnvironmentParameter(name="RH", current=self._humidity * 100.0),
    }
