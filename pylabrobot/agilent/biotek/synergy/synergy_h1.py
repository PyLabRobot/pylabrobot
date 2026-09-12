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

  async def _read_until(
    self, terminator: bytes, timeout: Optional[float] = None, chunk_size: int = 512
  ) -> bytes:
    if timeout is None:
      timeout = self.timeout

    buf = bytearray()

    retries = 0
    max_retries = 3

    try:
      with anyio.fail_after(timeout):
        while True:
          try:
            buf.extend(await self.io.read(chunk_size))
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
                f"{self.__class__.__name__} too many FtdiError retries ({max_retries}) — stopping",
                e,
              )
              raise
            await anyio.sleep(0.05)
    except TimeoutError:
      logger.debug(
        f"{self.__class__.__name__} _read_until timed out; partial buffer (hex): %s", buf.hex()
      )
      raise TimeoutError(
        f"{self.__class__.__name__} _read_until timed out waiting for {terminator!r}; partial={buf.hex()}"
      ) from None
