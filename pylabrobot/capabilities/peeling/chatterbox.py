import logging
from typing import Optional

from pylabrobot.serializer import SerializableMixin

from .backend import PeelerBackend

logger = logging.getLogger(__name__)


class PeelerChatterboxBackend(PeelerBackend):
  """Chatterbox backend for device-free testing."""

  async def peel(self, backend_params: Optional[SerializableMixin] = None):
    logger.info("Running peel cycle.")

  async def restart(self, backend_params: Optional[SerializableMixin] = None):
    logger.info("Restarting peeler.")
