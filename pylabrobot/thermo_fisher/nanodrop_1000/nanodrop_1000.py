import asyncio
import logging
import math
from typing import Dict, List, Optional, Tuple

from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)


class ThermoFisherNanoDrop1000:
  VID = 0x2457
  PID = 0x1002
  EP_OUT = 0x02
  EP_IN_HEAVY = 0x82
  EP_IN_COMM = 0x87

  # The pedestal solenoid selects the optical path length. Released, the sample column
  # spans the full gap between the two fibre ends; energised, it pulls the lever onto a
  # fixed mechanical stop and compresses the column. There is a single stop, so there are
  # exactly two paths — confirmed on hardware, where no argument byte to 0x0F produces a
  # third position. Measured ratio between them is ~5x, as expected for 1.0 / 0.2.
  PATH_LONG_MM = 1.0
  PATH_SHORT_MM = 0.2

  # Absorbance is linear in path length, so microvolume results are conventionally quoted
  # against a 10 mm cuvette: x10 from the long path, x50 from the short one.
  REFERENCE_PATH_MM = 10.0

  # Threshold above which the long path is saturated and the short one should be used.
  # From the ND-1000 operating software release notes, V3.5.2: "Adjusted long path cutoff
  # to 1.2AU for column formation test".
  AUTORANGE_CUTOFF_AU = 1.2

  # Below ~235 nm this hardware is photon starved and the absorbance there is noise, so it
  # must not drive the auto-range decision.
  AUTORANGE_WINDOW_NM = (235.0, 750.0)

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

    # Baselines are held per path length: a sample measured on one path can only be
    # divided by a blank taken on that same path. Keyed by path length in mm, each entry
    # holding {"dark": [...], "blank": [...]}.
    self.baselines: Dict[float, Dict[str, List[float]]] = {}

  # dark_spectrum / blank_spectrum predate path selection. Back then every acquisition ran
  # with the magnet energised, i.e. on the short path, so mapping these onto the
  # short-path baseline preserves their original meaning exactly.
  @property
  def dark_spectrum(self) -> Optional[List[float]]:
    return self.baselines.get(self.PATH_SHORT_MM, {}).get("dark")

  @dark_spectrum.setter
  def dark_spectrum(self, value: Optional[List[float]]) -> None:
    self.baselines.setdefault(self.PATH_SHORT_MM, {})["dark"] = value

  @property
  def blank_spectrum(self) -> Optional[List[float]]:
    return self.baselines.get(self.PATH_SHORT_MM, {}).get("blank")

  @blank_spectrum.setter
  def blank_spectrum(self, value: Optional[List[float]]) -> None:
    self.baselines.setdefault(self.PATH_SHORT_MM, {})["blank"] = value

  @classmethod
  def _configure_usb_device(cls, device) -> None:
    device.set_configuration()
    # clear_halt is not universally safe: on macOS libusb raises "Entity not found" for
    # an endpoint that is not actually halted, which aborts setup() before it starts.
    # seabreeze, the reference Ocean Optics stack for this hardware, does not call it at
    # all. Best-effort per endpoint.
    for ep in (cls.EP_OUT, cls.EP_IN_HEAVY, cls.EP_IN_COMM):
      try:
        device.clear_halt(ep)
      except Exception:
        logger.debug("clear_halt(0x%02x) failed; continuing", ep, exc_info=True)

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

  async def stop(self):
    """Safely powers down hardware and releases the USB port."""
    if self._connected:
      try:
        # Ensure lamp and magnet are off before disconnect
        await self.send_command([0x03, 0x00])
        await self.send_command([0x0F, 0x00])
        # On this firmware the lamp latch clears on a USB bus reset, not on [0x03, 0x00]
        # alone, as the original driver did. Teardown only, so the re-enumeration a reset
        # triggers is harmless here — we are disconnecting anyway.
        if self.io.dev is not None:
          await asyncio.to_thread(self.io.dev.reset)
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

  def _calculate_x_axis(self) -> List[float]:
    c0, c1 = self.coefficients.get(1, 0), self.coefficients.get(2, 0)
    c2, c3 = self.coefficients.get(3, 0), self.coefficients.get(4, 0)
    return [c0 + (c1 * pixel) + (c2 * (pixel**2)) + (c3 * (pixel**3)) for pixel in range(2048)]

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

  async def _select_path(self, path_mm: float) -> None:
    """Seat the pedestal solenoid for the requested optical path length."""
    if path_mm not in (self.PATH_LONG_MM, self.PATH_SHORT_MM):
      raise ValueError(
        f"path_mm must be {self.PATH_LONG_MM} or {self.PATH_SHORT_MM}, got {path_mm}"
      )
    await self.set_magnet(path_mm == self.PATH_SHORT_MM)
    await asyncio.sleep(0.2)

  async def take_blank(self, integration_ms=20, paths: Optional[Tuple[float, ...]] = None):
    """Acquire dark and blank baselines, by default for both path lengths.

    A sample measured on one path cannot be divided by a blank taken on another, so
    measuring both paths requires blanking both. Doing it here costs one extra pair of
    reads and lets the caller switch paths later without re-blanking.

    The long path is always taken first: compressing a liquid column is safe, letting it
    expand again can break it.
    """
    if paths is None:
      paths = (self.PATH_LONG_MM, self.PATH_SHORT_MM)

    await self._set_integration_time(integration_ms)

    for path_mm in sorted(paths, reverse=True):
      await self.set_lamp(False)
      await self._select_path(path_mm)
      logger.info("Acquiring dark baseline at %s mm", path_mm)
      dark = await self.get_raw_spectrum()

      await self.set_lamp(True)
      await asyncio.sleep(0.2)
      logger.info("Acquiring blank baseline at %s mm", path_mm)
      blank = await self.get_raw_spectrum()
      await self.set_lamp(False)

      self.baselines[path_mm] = {"dark": dark, "blank": blank}

    await self.set_magnet(False)
    logger.info("Blanking complete for paths: %s mm", sorted(self.baselines))

  def _absorbance(self, sample_spectrum: List[float], path_mm: float) -> List[float]:
    """Beer-Lambert absorbance against the baseline recorded for this path length."""
    baseline = self.baselines.get(path_mm) or {}
    if baseline.get("dark") is None or baseline.get("blank") is None:
      raise ValueError(
        f"No blank for the {path_mm} mm path. Run take_blank() first "
        f"(baselines held for: {sorted(self.baselines)} mm)."
      )

    return [
      -math.log10(max(sample - dark, 1) / max(blank - dark, 1))
      for sample, dark, blank in zip(sample_spectrum, baseline["dark"], baseline["blank"])
    ]

  def _peak_absorbance(self, wavelengths: List[float], absorbance: List[float]) -> float:
    """Highest absorbance within the trustworthy spectral window."""
    low, high = self.AUTORANGE_WINDOW_NM
    in_window = [
      value for wavelength, value in zip(wavelengths, absorbance) if low <= wavelength <= high
    ]
    return max(in_window) if in_window else 0.0

  @classmethod
  def to_path_length(
    cls, absorbance: List[float], from_mm: float, to_mm: Optional[float] = None
  ) -> List[float]:
    """Rescale absorbance to an equivalent path length.

    Beer-Lambert is linear in path length, so this is the conventional way to quote a
    microvolume reading against a cuvette: x10 from the long path, x50 from the short.
    """
    factor = (cls.REFERENCE_PATH_MM if to_mm is None else to_mm) / from_mm
    return [value * factor for value in absorbance]

  def select_path(self, wavelengths: List[float], spectra: Dict[float, List[float]]) -> float:
    """Pick the path to quantify from, given a reading on each.

    The longest path still under the auto-range cutoff, because a longer path resolves
    small differences better. If every path is over the cutoff the sample is too
    concentrated for any of them and the result should not be trusted; the shortest is
    returned as the least-bad option.
    """
    usable = [
      path_mm
      for path_mm, absorbance in spectra.items()
      if self._peak_absorbance(wavelengths, absorbance) <= self.AUTORANGE_CUTOFF_AU
    ]
    if not usable:
      logger.warning(
        "Every path exceeds %s AU; the sample is too concentrated to quantify. "
        "Dilute it, or treat the result as a lower bound.",
        self.AUTORANGE_CUTOFF_AU,
      )
      return min(spectra)
    return max(usable)

  async def _acquire_sample(self, path_mm: float) -> List[float]:
    await self._select_path(path_mm)
    await self.set_lamp(True)
    await asyncio.sleep(0.2)
    logger.info("Measuring sample at %s mm", path_mm)
    spectrum = await self.get_raw_spectrum()
    await self.set_lamp(False)
    return spectrum

  async def measure_absorbance(
    self, integration_ms=20, path="both"
  ) -> Tuple[List[float], Dict[float, List[float]]]:
    """Measure absorbance on each optical path.

    A normal run measures BOTH paths, as the instrument itself does — the vendor UV-Vis
    screen plots the long and short paths together from one measurement cycle. Both
    readings come from a single sample loading: the column is never touched between them,
    only compressed.

    `path` is "both" (default), "auto", "long", "short", or a path length in mm. "auto"
    reads the long path and only compresses to the short one if the long exceeds
    AUTORANGE_CUTOFF_AU, saving a read when the sample is dilute.

    Returns (wavelengths, {path_mm: absorbance}) — always keyed by path length, whether
    one path or both, because an absorbance without its path cannot be interpreted or
    compared. `select_path()` picks which to quantify from; `to_path_length()` rescales it
    to a 10 mm cuvette equivalent.
    """
    # TODO [Future Work]: Auto-Exposure Bracketing (HDR)
    # Replace the static `integration_ms` with a loop that fires 8ms, 16ms, 32ms, etc.
    # and mathematically stitches the optimal exposures together.

    if isinstance(path, str):
      requested = {
        "both": (self.PATH_LONG_MM, self.PATH_SHORT_MM),
        "auto": None,
        "long": (self.PATH_LONG_MM,),
        "short": (self.PATH_SHORT_MM,),
      }
      if path not in requested:
        raise ValueError(
          f"path must be 'both', 'auto', 'long', 'short' or a length in mm, got {path!r}"
        )
      paths = requested[path]
    else:
      paths = (float(path),)

    await self._set_integration_time(integration_ms)
    wavelengths = self._calculate_x_axis()
    spectra: Dict[float, List[float]] = {}

    try:
      if paths is not None:
        # Longest first: compressing a liquid column is safe, letting it expand can break
        # it, so descending order never re-forms the column mid-measurement.
        for path_mm in sorted(paths, reverse=True):
          spectra[path_mm] = self._absorbance(await self._acquire_sample(path_mm), path_mm)
        return wavelengths, spectra

      # Auto: read the long path, and only compress if it saturated.
      path_mm = self.PATH_LONG_MM
      spectra[path_mm] = self._absorbance(await self._acquire_sample(path_mm), path_mm)
      peak = self._peak_absorbance(wavelengths, spectra[path_mm])

      if peak > self.AUTORANGE_CUTOFF_AU:
        logger.info(
          "Long path peaked at %.3f AU (> %.2f); reading the %s mm path instead",
          peak,
          self.AUTORANGE_CUTOFF_AU,
          self.PATH_SHORT_MM,
        )
        short = self.PATH_SHORT_MM
        spectra = {short: self._absorbance(await self._acquire_sample(short), short)}

      return wavelengths, spectra
    finally:
      await self.set_lamp(False)
      await self.set_magnet(False)
