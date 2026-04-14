import logging
from typing import Optional

from pylabrobot.serializer import SerializableMixin

from .backend import CentrifugeBackend

logger = logging.getLogger(__name__)


class CentrifugeChatterboxBackend(CentrifugeBackend):
  """Chatterbox backend for device-free testing."""

  async def open_door(self) -> None:
    logger.info("Opening centrifuge door.")

  async def close_door(self) -> None:
    logger.info("Closing centrifuge door.")

  async def lock_door(self) -> None:
    logger.info("Locking centrifuge door.")

  async def unlock_door(self) -> None:
    logger.info("Unlocking centrifuge door.")

  async def go_to_bucket1(self) -> None:
    logger.info("Rotating to bucket 1.")

  async def go_to_bucket2(self) -> None:
    logger.info("Rotating to bucket 2.")

  async def lock_bucket(self) -> None:
    logger.info("Locking bucket.")

  async def unlock_bucket(self) -> None:
    logger.info("Unlocking bucket.")

  async def spin(
    self,
    g: float,
    duration: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> None:
    logger.info("Spinning at %s g for %s seconds.", g, duration)
