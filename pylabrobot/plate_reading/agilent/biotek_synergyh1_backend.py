import asyncio
import logging
import time
from typing import Optional

from pylibftdi import FtdiError

from pylabrobot.plate_reading.agilent.biotek_backend import BioTekPlateReaderBackend

logger = logging.getLogger(__name__)


class SynergyH1Backend(BioTekPlateReaderBackend):
  """Backend for Agilent BioTek Synergy H1 plate readers."""

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

    deadline = time.time() + timeout
    buf = bytearray()

    retries = 0
    max_retries = 3

    while True:
      if time.time() > deadline:
        logger.debug(
          f"{self.__class__.__name__} _read_until timed out; partial buffer (hex): %s", buf.hex()
        )
        raise TimeoutError(
          f"{self.__class__.__name__} _read_until timed out waiting for {terminator!r}; partial={buf.hex()}"
        )

      try:
        data = await self.io.read(chunk_size)
        if len(data) == 0:
          await asyncio.sleep(0.02)
          continue

        buf.extend(data)

        if terminator in buf:
          idx = buf.index(terminator) + len(terminator)
          full = bytes(buf[:idx])
          logger.debug(
            f"{self.__class__.__name__} _read_until received %d bytes (hex prefix): %s",
            len(full),
            full[:200].hex(),
          )
          return full

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
