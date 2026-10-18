import asyncio
import logging
import time
from typing import Optional

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

  async def _read_until(
    self, terminator: bytes, timeout: Optional[float] = None, chunk_size: int = 512
  ) -> bytes:
    if timeout is None:
      timeout = self.timeout

    deadline = time.monotonic() + timeout

    retries = 0
    max_retries = 3

    while True:
      response = self._pop_response(terminator)
      if response is not None:
        return response
      if time.monotonic() > deadline:
        logger.debug(
          f"{self.__class__.__name__} _read_until timed out; partial buffer (hex): %s",
          self._receive_buffer.hex(),
        )
        raise TimeoutError(
          f"{self.__class__.__name__} _read_until timed out waiting for {terminator!r}; "
          f"partial={self._receive_buffer.hex()}"
        )

      try:
        data = await self.io.read(chunk_size)
        if len(data) == 0:
          await asyncio.sleep(0.02)
          continue

        self._receive_buffer.extend(data)

      except FtdiError as e:
        retries += 1
        logger.warning(
          f"{self.__class__.__name__} transient FtdiError while reading: %s — retrying", e
        )

        if retries >= max_retries:
          logger.warning(
            f"{self.__class__.__name__} too many FtdiError retries ({max_retries}) — stopping", e
          )
          raise

        await asyncio.sleep(0.05)
        continue
      except Exception:
        raise
