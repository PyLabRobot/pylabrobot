import logging
from typing import TYPE_CHECKING, Optional

from pylabrobot.capabilities.capability import BackendParams

from .backend import LoadingTrayBackend

if TYPE_CHECKING:
  from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)


class LoadingTrayChatterboxBackend(LoadingTrayBackend):
  """Chatterbox backend for device-free testing."""

  async def open(self, backend_params: Optional[BackendParams] = None):
    logger.info("Opening loading tray.")

  async def close(
    self,
    backend_params: Optional[BackendParams] = None,
    resource: Optional["Resource"] = None,
  ):
    logger.info("Closing loading tray.")
