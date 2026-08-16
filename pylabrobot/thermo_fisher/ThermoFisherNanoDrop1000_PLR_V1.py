import asyncio
import logging
import math
from typing import List, Optional, Tuple

from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)


class ThermoFisherNanoDrop1000:
  VID = 0x2457
  PID = 0x1002
  EP_OUT = 0x02
  EP_IN_HEAVY = 0x82
  EP_IN_COMM = 0x87

  def __init__(self):
    self.io = USB(
      id_vendor=self.VID,
      id_product=self.PID,
      human_readable_device_name="Thermo Fisher NanoDrop 1000",
      packet_read_timeout=0.05,
      read_timeout=1,
      read_endpoint_address=self.EP_IN_COMM,
      write_endpoint_address=self.EP_OUT,
      configuration_callback=self._configure_usb_device,
    )
    self._connected = False

    self.coefficients = {}
    self.wavelengths = None
    self.dark_spectrum = None
    self.blank_spectrum = None

  @classmethod
  def _configure_usb_device(cls, device) -> None:
    device.set_configuration()
    device.clear_halt(cls.EP_OUT)
    device.clear_halt(cls.EP_IN_HEAVY)
    device.clear_halt(cls.EP_IN_COMM)

  async def setup(self):
    """Initializes the USB connection."""
    logger.info("Connecting to NanoDrop 1000")
    await self.io.setup(empty_buffer=False)
    self._connected = True

    # Wake & Init
    await self.send_command([0x08])
    await asyncio.sleep(0.1)
    await self.send_command([0x01])
    await asyncio.sleep(0.2)

    await self._download_all_coefficients()
    self._calculate_x_axis()

  async def stop(self):
    """Safely powers down hardware and releases the USB port."""
    if self._connected:
      try:
        # Ensure lamp and magnet are off before disconnect
        await self.send_command([0x03, 0x00])
        await self.send_command([0x0F, 0x00])
      except Exception:
        logger.warning("Failed to power down the NanoDrop cleanly", exc_info=True)
      await self.io.stop()
      self._connected = False
      logger.info("NanoDrop 1000 disconnected")

    self.coefficients = {}

  async def send_command(self, payload: List[int]):
    """Generic transport method for writing to the command mailbox."""
    await self.io.write(bytes(payload))

  async def read_comm(self, timeout=500) -> bytes:
    """Reads from the 64-byte text/status endpoint."""
    return await self.io.read(timeout=timeout / 1000, size=64)

  async def read_heavy(self, packets=64, timeout=1000) -> bytearray:
    """Reads bulk interleaved blocks from the main camera endpoint."""
    data_buffer = bytearray()
    for _ in range(packets):
      packet = await self.io.read(
        timeout=timeout / 1000,
        size=64,
        endpoint=self.EP_IN_HEAVY,
      )
      data_buffer.extend(packet)
    return data_buffer

  async def flush_comm(self):
    await self.io.drain(endpoint=self.EP_IN_COMM, timeout=0.05, size=64)

  async def flush_heavy(self):
    await self.io.drain(endpoint=self.EP_IN_HEAVY, timeout=0.05, size=512)

  async def set_lamp(self, state: bool):
    cmd = 0xFF if state else 0x00
    await self.send_command([0x03, cmd])

  async def set_magnet(self, state: bool):
    cmd = 0xFF if state else 0x00
    await self.send_command([0x0F, cmd])

  async def _set_integration_time(self, ms: int):
    if ms < 3:
      ms = 3
      logger.warning("Integration time is too low; using 3 ms")
    elif ms > 65535:
      ms = 65535
      logger.warning("Integration time is too high; using 65535 ms")

    lsb = ms & 0xFF
    msb = (ms >> 8) & 0xFF
    await self.send_command([0x02, lsb, msb])

  async def _download_all_coefficients(self):
    logger.info("Downloading NanoDrop factory memory map")
    await self.flush_comm()

    for index in range(1, 15):
      if index == 5:
        continue
      await self.send_command([0x05, index])
      await asyncio.sleep(0.05)
      try:
        data = await self.read_comm()
        text = bytearray(data[2:]).decode("ascii", errors="ignore").split("\x00")[0]
        self.coefficients[index] = float(text)
      except Exception:
        logger.warning("Failed to read coefficient index %d", index, exc_info=True)

  def _calculate_x_axis(self):
    c0, c1 = self.coefficients.get(1, 0), self.coefficients.get(2, 0)
    c2, c3 = self.coefficients.get(3, 0), self.coefficients.get(4, 0)
    self.wavelengths = [
      c0 + (c1 * pixel) + (c2 * (pixel**2)) + (c3 * (pixel**3)) for pixel in range(2048)
    ]

  async def get_raw_spectrum(self) -> List[float]:
    await self.flush_heavy()
    await self.send_command([0x09])

    data_buffer = await self.read_heavy()

    pixels = []
    for i in range(0, 4096, 128):
      lsb_block = data_buffer[i : i + 64]
      msb_block = data_buffer[i + 64 : i + 128]
      for j in range(64):
        pixels.append((msb_block[j] << 8) | lsb_block[j])

    raw_intensities = [float(pixel) for pixel in pixels]

    # TODO [Future Work]: Optical Black Pixel Subtraction
    # The first 25 pixels (0-24) are optically black. Calculate their average
    # and subtract it from the entire array to correct for thermal baseline drift.

    # TODO [Future Work]: Non-Linearity Correction
    # Apply the 7th-order polynomial using coefficients 6 through 13 to `raw_intensities`
    # to ensure perfect photometric accuracy across the dynamic range.

    return raw_intensities

  async def take_blank(self, integration_ms=20):
    await self._set_integration_time(integration_ms)

    await self.set_lamp(False)
    await self.set_magnet(True)
    await asyncio.sleep(0.2)
    logger.info("Acquiring dark baseline")
    self.dark_spectrum = await self.get_raw_spectrum()

    await self.set_lamp(True)
    await asyncio.sleep(0.2)
    logger.info("Acquiring blank baseline")
    self.blank_spectrum = await self.get_raw_spectrum()

    await self.set_lamp(False)
    await self.set_magnet(False)
    logger.info("Blanking complete")

  async def measure_absorbance(self, integration_ms=20) -> Tuple[List[float], List[float]]:
    if self.blank_spectrum is None or self.dark_spectrum is None:
      raise ValueError("You must run take_blank() before measuring!")

    # TODO [Future Work]: Auto-Exposure Bracketing (HDR)
    # Replace the static `integration_ms` with a loop that fires 8ms, 16ms, 32ms, etc.
    # and mathematically stitches the optimal exposures together.

    await self._set_integration_time(integration_ms)
    await self.set_magnet(True)
    await self.set_lamp(True)
    await asyncio.sleep(0.2)

    logger.info("Measuring sample")
    sample_spectrum = await self.get_raw_spectrum()

    await self.set_lamp(False)
    await self.set_magnet(False)

    absorbance = [
      -math.log10(max(sample - dark, 1) / max(blank - dark, 1))
      for sample, dark, blank in zip(
        sample_spectrum,
        self.dark_spectrum,
        self.blank_spectrum,
      )
    ]

    return self.wavelengths, absorbance
