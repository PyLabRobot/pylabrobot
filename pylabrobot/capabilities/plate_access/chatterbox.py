import logging
from typing import Optional

from .backend import PlateAccessBackend, PlateAccessState

logger = logging.getLogger(__name__)


class PlateAccessChatterboxBackend(PlateAccessBackend):
  """Chatterbox backend for device-free testing."""

  def __init__(self):
    self._locked = False
    self._state = PlateAccessState(
      source_access_open=False,
      source_access_closed=True,
      destination_access_open=False,
      destination_access_closed=True,
      door_open=False,
      door_closed=True,
      source_plate_position=0,
      destination_plate_position=0,
    )

  async def lock(self, app: Optional[str] = None, owner: Optional[str] = None) -> None:
    logger.info("Locking plate access backend with app=%s owner=%s.", app, owner)
    self._locked = True

  async def unlock(self) -> None:
    logger.info("Unlocking plate access backend.")
    self._locked = False

  async def get_access_state(self) -> PlateAccessState:
    logger.info("Returning chatterbox access state.")
    return self._state

  async def open_source_plate(self, timeout: Optional[float] = None) -> None:
    logger.info("Opening source-side access.")
    self._state.source_access_open = True
    self._state.source_access_closed = False
    self._state.source_plate_position = -1
    self._state.door_open = True
    self._state.door_closed = False

  async def close_source_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> Optional[str]:
    logger.info(
      "Closing source-side access with plate_type=%s barcode_location=%s barcode=%s timeout=%s.",
      plate_type,
      barcode_location,
      barcode,
      timeout,
    )
    self._state.source_access_open = False
    self._state.source_access_closed = True
    self._state.source_plate_position = 0
    return None

  async def open_destination_plate(self, timeout: Optional[float] = None) -> None:
    logger.info("Opening destination-side access.")
    self._state.destination_access_open = True
    self._state.destination_access_closed = False
    self._state.destination_plate_position = -1
    self._state.door_open = True
    self._state.door_closed = False

  async def close_destination_plate(
    self,
    plate_type: Optional[str] = None,
    barcode_location: Optional[str] = None,
    barcode: str = "",
    timeout: Optional[float] = None,
  ) -> Optional[str]:
    logger.info(
      "Closing destination-side access with plate_type=%s barcode_location=%s barcode=%s timeout=%s.",
      plate_type,
      barcode_location,
      barcode,
      timeout,
    )
    self._state.destination_access_open = False
    self._state.destination_access_closed = True
    self._state.destination_plate_position = 0
    return None

  async def close_door(self, timeout: Optional[float] = None) -> None:
    logger.info("Closing plate access door.")
    self._state.door_open = False
    self._state.door_closed = True
