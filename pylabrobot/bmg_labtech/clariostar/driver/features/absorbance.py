"""Absorbance: optical density at up to 8 wavelengths, all in one pass over the plate."""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple

from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.resources import Plate, Well

from ..errors import FrameError
from .measurement import (
  Measurement,
  MeasurementConfiguration,
  MeasurementSettings,
  ShakeSettings,
  _get_measured_identifiers,
  _get_readings_by_well,
)

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

Report = Literal["optical_density", "raw"]
_REPORTS = ("optical_density", "raw")

# The decoded reply: the samples per wavelength, the references, the wavelengths' calibration
# pairs and the reference's.
_Decoded = Tuple[List[List[int]], List[int], List[Tuple[int, int]], Tuple[int, int]]


# ----------------------------------------
# Results and device facts
# ----------------------------------------


@dataclass
class CLARIOstarAbsorbanceResult(AbsorbanceResult):
  """A CLARIOstar absorbance result in raw counts: `data` holds the sample counts, with what
  computes their optical density, OD = -log10((sample / c) * (r / reference)).

  Attributes:
    references: the reference counts, keyed as ``data``.
    calibration: the wavelength's calibration pair; c is its first value.
    reference_calibration: the reference's calibration pair; r is its first value.
  """

  references: Optional[Dict[str, float]] = None
  calibration: Optional[Tuple[int, int]] = None
  reference_calibration: Optional[Tuple[int, int]] = None


@dataclass
class CLARIOstarAbsorbanceSpectrumResult(AbsorbanceSpectrumResult):
  """A CLARIOstar absorbance spectrum in raw counts: `data` holds each well's sample counts by
  wavelength, with what computes their optical densities (see `CLARIOstarAbsorbanceResult`).

  Attributes:
    references: the reference counts, keyed by well identifier; the same for every wavelength.
    calibration: each wavelength's calibration pair, keyed by wavelength in nm.
    reference_calibration: the reference's calibration pair.
  """

  references: Optional[Dict[str, float]] = None
  calibration: Optional[Dict[int, Tuple[int, int]]] = None
  reference_calibration: Optional[Tuple[int, int]] = None


@dataclass
class AbsorbanceConfiguration(MeasurementConfiguration):
  """Device facts of the absorbance measurement."""

  wavelength_range: Tuple[int, int] = (220, 1000)
  """Wavelengths a read accepts, in nm (the manufacturer's spectrometer range)."""
  maximum_wavelengths: int = 8
  """Wavelengths per read (up to 8; 8 read on hardware)."""


# ----------------------------------------
# Optical density
# ----------------------------------------


def _compute_readings(
  samples: List[List[int]],
  references: List[int],
  calibrations: List[Tuple[int, int]],
  reference_pair: Tuple[int, int],
  report: Report,
) -> List[List[float]]:
  """The readings per wavelength from a decoded data reply (see `Absorbance._decode_data_reply`).

  OD = -log10((sample / c) * (r / reference)), with c and r the first value of the wavelength's and
  of the reference's calibration pair. A sample of 0 (no light through the well) reads as infinite
  optical density. Raw readings are the sample counts.

  Raises:
    ValueError: unless raw, if a calibration or reference is 0.
  """
  reference_calibration = reference_pair[0]
  if report == "raw":
    return [[float(sample) for sample in group] for group in samples]
  if reference_calibration == 0 or 0 in references or any(c == 0 for c, _ in calibrations):
    raise ValueError("absorbance reply has a calibration or reference of 0")
  readings = []
  for group, (calibration, _) in zip(samples, calibrations):
    transmittances = [
      (sample / calibration) * (reference_calibration / reference)
      for sample, reference in zip(group, references)
    ]
    readings.append(
      [
        -math.log10(transmittance) if transmittance else math.inf
        for transmittance in transmittances
      ]
    )
  return readings


def _get_spectrum_wavelengths(
  start_wavelength: int, end_wavelength: int, step_size: int
) -> List[int]:
  """The wavelengths a spectrum scans, in nm."""
  return list(range(start_wavelength, end_wavelength + 1, step_size))


class Absorbance(Measurement):
  """Absorbance measurement.

  Reached as `driver.absorbance`, built at setup only if the plate reader reports it is
  capable of performing transmittance measurements which can be converted to absorbance.
  """

  configuration: AbsorbanceConfiguration

  _MODE = 0x02
  _WELL_SCAN_CODE = 0x02
  _TRAILER_START = 0x02

  def __init__(
    self, driver: "CLARIOstarDriver", configuration: Optional[AbsorbanceConfiguration] = None
  ):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the absorbance's device facts. Defaults to `AbsorbanceConfiguration()`.
    """
    super().__init__(driver, configuration or AbsorbanceConfiguration())

  # ----------------------------------------
  # RUN fields
  # ----------------------------------------

  # Water pathlength correction after the wavelength fields, in nm: two wavelengths, a third
  # unused, and its bandwidth, always sent.
  _PATHLENGTH_CORRECTION = (900.0, 993.0, 0.0, 10.0)
  # Pause, u16 BE each: when (0 off, 1 before plate reading), then seconds; off, 5 s are sent,
  # and on, 5 s is verified.
  _NO_PAUSE = bytes.fromhex("00000005")
  _PAUSE_BEFORE_READING = 0x0001

  def _compute_pause(self, pause_before_reading: Optional[int]) -> bytes:
    """The pause field: none, or `pause_before_reading` seconds before the plate is read."""
    if pause_before_reading is None:
      return self._NO_PAUSE
    return self._PAUSE_BEFORE_READING.to_bytes(2, "big") + pause_before_reading.to_bytes(2, "big")

  @staticmethod
  def _encode_wavelengths(*nanometres: float) -> bytes:
    """Each wavelength as u16 BE in 0.1 nm."""
    return b"".join(round(nm * 10).to_bytes(2, "big") for nm in nanometres)

  # ----------------------------------------
  # Decoding
  # ----------------------------------------

  # Data reply layout byte [6]: the only one verified, with the temperature controller on or off.
  _DATA_LAYOUT = 0xA9

  @staticmethod
  def _split_values(values: List[int], wavelength_count: int, wells: int) -> _Decoded:
    """The samples per wavelength, the references, the wavelengths' calibration pairs and the
    reference's, from n + 3 groups of one value per well, then n + 3 calibration pairs."""
    groups = wavelength_count + 3
    group = [values[k * wells : (k + 1) * wells] for k in range(groups)]
    pairs = [
      (values[groups * wells + 2 * k], values[groups * wells + 2 * k + 1]) for k in range(groups)
    ]
    return group[:wavelength_count], group[-1], pairs[:wavelength_count], pairs[-1]

  def _decode_data_reply(self, reply: bytes, wavelengths: List[int]) -> _Decoded:
    """The samples per wavelength and the references, in the reply's well order (row by row), the
    wavelengths' calibration pairs and the reference's.

    The reply holds n + 3 groups of one u32 per well (the n samples, two groups not decoded,
    the reference), then n + 3 calibration pairs, in the same order.

    Raises:
      ValueError: if the reply is incomplete or not the layout of this read.
    """
    if len(reply) < 36:
      raise ValueError(f"absorbance reply too short: {len(reply)} bytes")
    if reply[6] != self._DATA_LAYOUT:
      raise ValueError(f"absorbance reply layout 0x{reply[6]:02x} is not decoded")
    wells = int.from_bytes(reply[20:22], "big")
    positions = int.from_bytes(reply[23:25], "big")
    groups = len(wavelengths) + 3
    expected = groups * (wells + 2)
    if int.from_bytes(reply[7:9], "big") != expected or positions != 1:
      raise ValueError(
        f"absorbance reply announces {int.from_bytes(reply[7:9], 'big')} values at {positions} "
        f"position(s) per well; expected {expected} at 1"
      )
    if int.from_bytes(reply[9:11], "big") != expected or len(reply) < 36 + 4 * expected:
      raise ValueError("absorbance reply is incomplete")
    values = [int.from_bytes(reply[36 + 4 * i : 40 + 4 * i], "big") for i in range(expected)]
    return self._split_values(values, len(wavelengths), wells)

  def _decode_spectrum_pages(self, pages: List[bytes], wavelength_count: int) -> _Decoded:
    """As `_decode_data_reply`, for a spectrum's data pages.

    Each page has the discrete header with its own value count at bytes 7-8, its page number at 29
    and n + 2 modulo 256 at 17; its values follow. All pages' values together are the discrete
    layout.

    Raises:
      ValueError: if a page is not a page of this read, in order, or values are missing.
    """
    values: List[int] = []
    wells: Optional[int] = None
    for number, page in enumerate(pages, 1):
      if len(page) < 36 or page[6] != self._DATA_LAYOUT:
        raise ValueError(f"absorbance spectrum page {number} is not an absorbance data page")
      count = int.from_bytes(page[7:9], "big")
      page_wells = int.from_bytes(page[20:22], "big")
      if (
        page[29] != number
        or page[17] != (wavelength_count + 2) & 0xFF
        or int.from_bytes(page[23:25], "big") != 1
        or (wells is not None and page_wells != wells)
        or len(page) != 36 + 4 * count
      ):
        raise ValueError(f"absorbance spectrum page {number} is not page {number} of this read")
      wells = page_wells
      values += [int.from_bytes(page[36 + 4 * i : 40 + 4 * i], "big") for i in range(count)]
    expected = (wavelength_count + 3) * ((wells or 0) + 2)
    if wells is None or len(values) != expected:
      raise ValueError(f"absorbance spectrum data holds {len(values)} values; expected {expected}")
    return self._split_values(values, wavelength_count, wells)

  # ----------------------------------------
  # Discrete reads
  # ----------------------------------------

  # A discrete read's end and step fields: end 0, and the spectrometer's 10 nm bandwidth in the
  # step field (`00 00 00 64`).
  _DISCRETE_END = 0.0
  _DISCRETE_BANDWIDTH = 10.0

  def _check_arguments(
    self,
    wavelengths: Sequence[int],
    report: str,
    measurement: MeasurementSettings,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise if the wavelengths, the report, the well scan or the timeout are out of range.

    Raises:
      ValueError: listing every violation: not 1 to `maximum_wavelengths` wavelengths, a wavelength
        not a whole nm within `wavelength_range`, an unknown report, a matrix well scan (not
        decoded), a timeout not positive.
    """
    errors = []
    maximum = self.configuration.maximum_wavelengths
    if not 1 <= len(wavelengths) <= maximum:
      errors.append(f"1 to {maximum} wavelengths, got {len(wavelengths)}")
    lowest, highest = self.configuration.wavelength_range
    for wavelength in wavelengths:
      if not isinstance(wavelength, int) or not lowest <= wavelength <= highest:
        errors.append(f"wavelengths must be whole {lowest} to {highest} nm, got {wavelength!r}")
    if report not in _REPORTS:
      errors.append(f"report must be one of {list(_REPORTS)}, got {report!r}")
    if measurement.well_scan == "matrix":
      errors.append("a matrix well scan is not supported for absorbance")
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    if errors:
      raise ValueError("; ".join(errors))

  def _compute_run_parameters(
    self,
    plate: Plate,
    wells: Sequence[Well],
    wavelengths: Sequence[int],
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
  ) -> bytes:
    """The RUN parameters of an absorbance read: the wavelength count and the wavelengths, then
    the end and step fields a spectrum uses (end 0, the spectrometer's bandwidth as the step)."""
    return (
      self._compute_run_start(plate, wells, measurement, shake)
      + bytes([len(wavelengths)])
      + self._encode_wavelengths(*wavelengths, self._DISCRETE_END, self._DISCRETE_BANDWIDTH)
      + self._encode_wavelengths(*self._PATHLENGTH_CORRECTION)
      + self._compute_pause(measurement.pause_before_reading)
      + self._compute_run_end(measurement.flashes)
    )

  def _get_run_parameters(
    self,
    wavelengths: Sequence[int],
    report: str,
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
    timeout: Optional[float] = None,
  ) -> Tuple[Plate, List[Well], bytes]:
    """The plate, the wells and the RUN parameters of a measurement, every argument checked.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if the wavelengths, report or well scan are out of range (`_check_arguments`),
        or the settings cannot read the wells of the plate on the reader
        (`_get_plate_and_wells`).
    """
    self._check_arguments(wavelengths, report, measurement, timeout)
    plate, wells = self._get_plate_and_wells(measurement, shake, wells)
    parameters = self._compute_run_parameters(plate, wells, wavelengths, measurement, shake)
    return plate, wells, parameters

  async def measure(
    self,
    *,
    wells: Optional[List[Well]] = None,
    wavelengths: Sequence[int],
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    report: Report = "optical_density",
    timeout: Optional[float] = None,
  ) -> List[AbsorbanceResult]:
    """Measure the plate in the plate holder at every wavelength, each well read once for all.

    Cancelling the call stops the run on the reader.

    Args:
      wells: the wells to measure, on the plate; None measures every well.
      wavelengths: 1 to `configuration.maximum_wavelengths` wavelengths, each a whole nm within
        `configuration.wavelength_range`; all read in one pass.
      measurement_settings: how to read; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      report: what the results hold: optical density, or the raw sample counts with the reference
        counts and calibration pairs that compute it (`CLARIOstarAbsorbanceResult`).
      timeout: seconds the run may take; `default_read_timeout` when None.

    Returns:
      one result per wavelength, in the order given.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without its data.
      ValueError: if the wavelengths or report are out of range, or the settings cannot read the
        wells of the plate.
      TimeoutError: if the run takes longer than `timeout` seconds.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    plate, wells, parameters = self._get_run_parameters(
      wavelengths, report, measurement, shake_settings, wells, timeout
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    logger.info(
      "absorbance: %s nm, %d of %d wells of %s, %s scan, %s",
      ", ".join(str(wavelength) for wavelength in wavelengths),
      len(wells),
      plate.num_items,
      plate.name,
      measurement.well_scan,
      report,
    )
    start_timestamp = datetime.now()
    replies, end_timestamp = await self._run_measurement(parameters, timeout)
    reply = replies[0]
    wavelengths = list(wavelengths)
    samples, references, calibrations, reference_pair = self._decode_data_reply(reply, wavelengths)
    readings = _compute_readings(samples, references, calibrations, reference_pair, report)
    # Reply bytes 34-35: the temperature in 0.1 C; 0 when the temperature controller is not
    # monitoring.
    raw_temperature = int.from_bytes(reply[34:36], "big")
    temperature = raw_temperature / 10 if raw_temperature else None
    results: List[AbsorbanceResult] = []
    for wavelength, values, calibration in zip(wavelengths, readings, calibrations):
      data = _get_readings_by_well(plate, wells, values)
      if report == "raw":
        reference_counts = _get_readings_by_well(plate, wells, [float(v) for v in references])
        results.append(
          CLARIOstarAbsorbanceResult(
            data=data,
            wavelength=wavelength,
            temperature=temperature,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            references=reference_counts,
            calibration=calibration,
            reference_calibration=reference_pair,
          )
        )
      else:
        results.append(
          AbsorbanceResult(
            data=data,
            wavelength=wavelength,
            temperature=temperature,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
          )
        )
    return results

  # ----------------------------------------
  # Spectra
  # ----------------------------------------

  def _check_spectrum_arguments(
    self,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    report: str,
    measurement: MeasurementSettings,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise if the spectrum's wavelengths, report, well scan or timeout are out of range.

    Raises:
      ValueError: listing every violation: an end not a whole nm within `wavelength_range`, an end
        not above the start, a step not a whole nm of at least 1 dividing the range, an unknown
        report, a matrix well scan, a timeout not positive.
    """
    errors = []
    lowest, highest = self.configuration.wavelength_range
    ends_valid = True
    for name, value in (("start_wavelength", start_wavelength), ("end_wavelength", end_wavelength)):
      if not isinstance(value, int) or not lowest <= value <= highest:
        errors.append(f"{name} must be a whole {lowest} to {highest} nm, got {value!r}")
        ends_valid = False
    if not isinstance(step_size, int) or step_size < 1:
      errors.append(f"step_size must be a whole nm of at least 1, got {step_size!r}")
    elif ends_valid and end_wavelength <= start_wavelength:
      errors.append(f"end_wavelength must be above start_wavelength, got {end_wavelength}")
    elif ends_valid and (end_wavelength - start_wavelength) % step_size:
      errors.append(
        f"step_size must divide {start_wavelength} to {end_wavelength} nm, got {step_size}"
      )
    if report not in _REPORTS:
      errors.append(f"report must be one of {list(_REPORTS)}, got {report!r}")
    if measurement.well_scan == "matrix":
      errors.append("a matrix well scan is not supported for absorbance")
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    if errors:
      raise ValueError("; ".join(errors))

  def _compute_spectrum_run_parameters(
    self,
    plate: Plate,
    wells: Sequence[Well],
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
  ) -> bytes:
    """The RUN parameters of an absorbance spectrum: count 0, then start, end and step."""
    return (
      self._compute_run_start(plate, wells, measurement, shake)
      + b"\x00"
      + self._encode_wavelengths(start_wavelength, end_wavelength, step_size)
      + self._encode_wavelengths(*self._PATHLENGTH_CORRECTION)
      + self._compute_pause(measurement.pause_before_reading)
      + self._compute_run_end(measurement.flashes)
    )

  def _get_spectrum_run_parameters(
    self,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    report: str,
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
    timeout: Optional[float] = None,
  ) -> Tuple[Plate, List[Well], bytes]:
    """The plate, the wells and the RUN parameters of a spectrum, every argument checked.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if the wavelengths, report or well scan are out of range
        (`_check_spectrum_arguments`), or the settings cannot read the wells of the plate.
    """
    self._check_spectrum_arguments(
      start_wavelength, end_wavelength, step_size, report, measurement, timeout
    )
    plate, wells = self._get_plate_and_wells(measurement, shake, wells)
    parameters = self._compute_spectrum_run_parameters(
      plate, wells, start_wavelength, end_wavelength, step_size, measurement, shake
    )
    return plate, wells, parameters

  async def measure_spectrum(
    self,
    *,
    wells: Optional[List[Well]] = None,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    report: Report = "optical_density",
    timeout: Optional[float] = None,
  ) -> AbsorbanceSpectrumResult:
    """Measure the absorbance spectrum of the plate in the plate holder, every wavelength in one
    pass.

    The data takes about 0.05 s per wavelength to transfer for 96 wells (220-1000 nm: about 40 s
    of a 2.5 min read); a larger `step_size` or a narrower range shortens it. Cancelling the call
    stops the run on the reader. A data page that arrives short ends the read, and the plate is
    measured once more (a new run: the reader cannot send a page again).

    Args:
      wells: the wells to measure, on the plate; None measures every well.
      start_wavelength: the first wavelength in nm, a whole nm within
        `configuration.wavelength_range`.
      end_wavelength: the last wavelength in nm, above `start_wavelength`, within the same range.
      step_size: nm between wavelengths, a whole nm of at least 1 dividing the range.
      measurement_settings: how to read; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      report: optical density, or the raw sample counts with the reference counts and calibration
        pairs that compute it (`CLARIOstarAbsorbanceSpectrumResult`).
      timeout: seconds the run may take; `default_read_timeout` when None.

    Returns:
      each measured well's spectrum, keyed by well identifier.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without its data.
      ValueError: if an argument is out of range, the settings cannot read the wells of the plate,
        or the data pages are not this read's.
      TimeoutError: if the run takes longer than `timeout` seconds.
      FrameError: if a data page arrives short on the second read too.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    plate, wells, parameters = self._get_spectrum_run_parameters(
      start_wavelength,
      end_wavelength,
      step_size,
      report,
      measurement,
      shake_settings,
      wells,
      timeout,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    wavelengths = _get_spectrum_wavelengths(start_wavelength, end_wavelength, step_size)
    measured = len(_get_measured_identifiers(plate, wells))
    logger.info(
      "absorbance spectrum: %d to %d nm in %d nm steps, %d of %d wells of %s, %s scan, %s",
      start_wavelength,
      end_wavelength,
      step_size,
      measured,
      plate.num_items,
      plate.name,
      measurement.well_scan,
      report,
    )
    values = (len(wavelengths) + 3) * (measured + 2)
    try:
      start_timestamp = datetime.now()
      pages, end_timestamp = await self._run_measurement(parameters, timeout, values)
    except FrameError as error:  # a page arrived short; the reader cannot send it again
      logger.warning("absorbance spectrum: %s; measuring the plate again", error)
      start_timestamp = datetime.now()
      pages, end_timestamp = await self._run_measurement(parameters, timeout, values)
    samples, references, calibrations, reference_pair = self._decode_spectrum_pages(
      pages, len(wavelengths)
    )
    readings = _compute_readings(samples, references, calibrations, reference_pair, report)
    # Page bytes 34-35: the temperature in 0.1 C; 0 when the temperature controller is not
    # monitoring.
    raw_temperature = int.from_bytes(pages[0][34:36], "big")
    temperature = raw_temperature / 10 if raw_temperature else None
    identifiers = _get_measured_identifiers(plate, wells)
    data: Dict[str, Dict[int, float]] = {identifier: {} for identifier in identifiers}
    for wavelength, wavelength_readings in zip(wavelengths, readings):  # row by row over the wells
      for identifier, value in zip(identifiers, wavelength_readings):
        data[identifier][wavelength] = value
    if report == "raw":
      return CLARIOstarAbsorbanceSpectrumResult(
        data=data,
        temperature=temperature,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        references=_get_readings_by_well(plate, wells, [float(v) for v in references]),
        calibration=dict(zip(wavelengths, calibrations)),
        reference_calibration=reference_pair,
      )
    return AbsorbanceSpectrumResult(
      data=data,
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )
