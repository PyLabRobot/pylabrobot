import logging
from typing import Optional

import anyio

try:
  from pylibftdi import FtdiError

  HAS_PYLIBFTDI = True
except ImportError:
  HAS_PYLIBFTDI = False
  FtdiError = Exception  # type: ignore[misc,assignment]

from pylabrobot.agilent.biotek.plate_reader_base import BioTekPlateReaderDriver
from pylabrobot.resources import Coordinate, PlateHolder

logger = logging.getLogger(__name__)


class SynergyH1(BioTekPlateReaderDriver):
  """Agilent BioTek Synergy H1 plate reader."""

  def __init__(
    self,
    name: str,
    timeout: float = 20,
    device_id: Optional[str] = None,
  ) -> None:
    super().__init__(
      timeout=timeout, device_id=device_id, human_readable_device_name="Agilent BioTek Synergy H1"
    )
    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,
      child_location=Coordinate.zero(),  # TODO: measure
    )

  @property
  def supports_heating(self):
    return True

  @property
  def supports_cooling(self):
    return False

  @property
  def focal_height_range(self):
    return (4.5, 10.68)

  async def _read_chunk(self, length: int) -> bytes:
    """Retry transient FTDI read errors without consuming bytes from the next reply."""
    retries = 0
    while True:
      try:
        return await super()._read_chunk(length)
      except FtdiError:
        retries += 1
        if retries == 3:
          raise
        logger.warning("Synergy H1 FTDI read failed; retrying", exc_info=True)
        await anyio.sleep(0.05)
