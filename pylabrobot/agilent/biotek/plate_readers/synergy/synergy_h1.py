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

from pylabrobot.agilent.biotek.plate_readers.base import BioTekBackend
from pylabrobot.capabilities.plate_reading.absorbance import Absorbance
from pylabrobot.capabilities.plate_reading.fluorescence import Fluorescence
from pylabrobot.capabilities.plate_reading.luminescence import Luminescence
from pylabrobot.capabilities.temperature_controlling import TemperatureController
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, PlateHolder, Resource

logger = logging.getLogger(__name__)


class SynergyH1Backend(BioTekBackend):
  """Backend for Agilent BioTek Synergy H1 plate readers."""

  def __init__(self, timeout: float = 20, device_id: Optional[str] = None) -> None:
    super().__init__(
      timeout=timeout, device_id=device_id, human_readable_device_name="Agilent BioTek Synergy H1"
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


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class SynergyH1(Resource, Device):
  """Agilent BioTek Synergy H1 plate reader."""

  def __init__(
    self,
    name: str,
    device_id: Optional[str] = None,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    backend = SynergyH1Backend(device_id=device_id)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="Agilent BioTek Synergy H1",
    )
    Device.__init__(self, driver=backend)
    self.driver: SynergyH1Backend = backend
    self.absorbance = Absorbance(backend=backend)
    self.luminescence = Luminescence(backend=backend)
    self.fluorescence = Fluorescence(backend=backend)
    self.temperature = TemperatureController(backend=backend)
    self._capabilities = [self.absorbance, self.luminescence, self.fluorescence, self.temperature]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,
      child_location=Coordinate.zero(),  # TODO: measure
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self, slow: bool = False) -> None:
    await self.driver.open(slow=slow)

  async def close(self, slow: bool = False) -> None:
    await self.driver.close(slow=slow)
