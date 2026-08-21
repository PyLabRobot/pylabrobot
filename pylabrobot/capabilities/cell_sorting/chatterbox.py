import logging
from typing import Optional

from pylabrobot.capabilities.capability import BackendParams

from .backend import CellSorterBackend

logger = logging.getLogger(__name__)


class CellSorterChatterboxBackend(CellSorterBackend):
  """Chatterbox backend for device-free testing. Logs every call, moves no fluid."""

  async def get_status(self) -> str:
    logger.info("Getting sorter status.")
    return "idle"

  async def load_template(self, name: str) -> None:
    logger.info("Loading sort template %s.", name)

  async def set_deposition(self, cells_per_well: int, plate_format: str) -> None:
    logger.info(
      "Setting deposition to %s cells/well on %s-well plate.", cells_per_well, plate_format
    )

  async def prime(self) -> None:
    logger.info("Priming fluidics.")

  async def start_sort(
    self,
    wells: int,
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    logger.info("Starting sort into %s wells.", wells)

  async def wait_for_completion(self, poll_interval: float, timeout: float) -> None:
    logger.info("Waiting for sort to complete (poll=%ss, timeout=%ss).", poll_interval, timeout)

  async def abort(self) -> None:
    logger.info("Aborting sort.")

  async def clean(self) -> None:
    logger.info("Running clean cycle.")
