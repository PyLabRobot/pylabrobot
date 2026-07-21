"""Chatterbox backend for device-free testing of diaphragm dispensers."""

import logging
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.resources import Container

from .backend import DiaphragmDispenserBackend

logger = logging.getLogger(__name__)


class DiaphragmDispenserChatterboxBackend(DiaphragmDispenserBackend):
  """Logs each capability call instead of talking to hardware.

  Useful for protocol unit tests and dry-runs without an instrument attached.
  """

  async def dispense(
    self,
    containers: List[Container],
    volumes: List[float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    for container, volume in zip(containers, volumes):
      logger.info("Dispensing %.2f uL into %s.", volume, container.name)

  async def prime(self, backend_params: Optional[BackendParams] = None) -> None:
    logger.info("Priming diaphragm dispenser.")
