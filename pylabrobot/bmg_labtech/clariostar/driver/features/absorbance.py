"""Absorbance: optical density at up to 8 wavelengths, all in one pass over the plate."""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple

from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.resources import Plate, Well

from .measurement import (
  MODE_ABSORBANCE,
  Measurement,
  MeasurementConfiguration,
  MeasurementSettings,
  ShakeSettings,
  _compute_run_end,
  _compute_run_start,
  _get_readings_by_well,
  _run_measurement,
)

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

Report = Literal["optical_density", "raw"]
_REPORTS = ("optical_density", "raw")

# Absorbance in the well-scan field (A02, B1).
_WELL_SCAN_CODE = 0x02
# The absorbance fields of a RUN, as the vendor sends its default endpoint read (DOE_Baseline).
_REFERENCE_BLOCK = bytes.fromhex("00000064232826ca00000064")
# Pause, u16 BE each: when (0 off, 1 before plate reading), then seconds. Off, the
# vendor sends 5 s (DOE_Baseline); on, 5 s captured (P01).
_NO_PAUSE = bytes.fromhex("00000005")
_PAUSE_BEFORE_READING = 0x0001
_TRAILER_START = 0x02

# Data reply layout byte [6]: the only one captured, with the temperature controller on or off.
_DATA_LAYOUT = 0xA9


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
class AbsorbanceConfiguration(MeasurementConfiguration):
  """Device facts of the absorbance measurement."""

  wavelength_range: Tuple[int, int] = (220, 1000)
  """Wavelengths a read accepts, in nm (the manufacturer's spectrometer range)."""
  maximum_wavelengths: int = 8
  """Wavelengths per read (the vendor's protocol fields WaveLength1-WaveLength8; 1-5 captured)."""


def _check_arguments(
  wavelengths: Sequence[int], report: str, configuration: AbsorbanceConfiguration
) -> None:
  """Raise if the wavelengths or the report are out of range.

  Raises:
    ValueError: listing every violation: not 1 to `maximum_wavelengths` wavelengths, a wavelength
      not a whole nm within `wavelength_range`, an unknown report.
  """
  errors = []
  maximum = configuration.maximum_wavelengths
  if not 1 <= len(wavelengths) <= maximum:
    errors.append(f"1 to {maximum} wavelengths, got {len(wavelengths)}")
  lowest, highest = configuration.wavelength_range
  for wavelength in wavelengths:
    if not isinstance(wavelength, int) or not lowest <= wavelength <= highest:
      errors.append(f"wavelengths must be whole {lowest} to {highest} nm, got {wavelength!r}")
  if report not in _REPORTS:
    errors.append(f"report must be one of {list(_REPORTS)}, got {report!r}")
  if errors:
    raise ValueError("; ".join(errors))


def _compute_pause(pause_before_reading: Optional[int]) -> bytes:
  """The pause field: none, or `pause_before_reading` seconds before the plate is read."""
  if pause_before_reading is None:
    return _NO_PAUSE
  return _PAUSE_BEFORE_READING.to_bytes(2, "big") + pause_before_reading.to_bytes(2, "big")


def _compute_run_parameters(
  plate: Plate,
  wells: Sequence[Well],
  wavelengths: Sequence[int],
  measurement: MeasurementSettings,
  shake: Optional[ShakeSettings],
  flashes: int,
) -> bytes:
  """The RUN parameters of an absorbance read."""
  return (
    _compute_run_start(plate, wells, MODE_ABSORBANCE, _WELL_SCAN_CODE, measurement, shake)
    + bytes([len(wavelengths)])
    + b"".join((wavelength * 10).to_bytes(2, "big") for wavelength in wavelengths)
    + _REFERENCE_BLOCK
    + _compute_pause(measurement.pause_before_reading)
    + _compute_run_end(_TRAILER_START, flashes)
  )


def _decode_data_reply(
  reply: bytes, wavelengths: List[int]
) -> Tuple[List[List[int]], List[int], List[Tuple[int, int]], Tuple[int, int]]:
  """The samples per wavelength and the references, in the reply's well order (row by row), the
  wavelengths' calibration pairs and the reference's.

  The reply holds n + 3 groups of one u32 per well (the n samples, chromatics 2 and 3, the
  reference), then n + 3 calibration pairs, in the same order.

  Raises:
    ValueError: if the reply is incomplete or not the layout of this read.
  """
  if len(reply) < 36:
    raise ValueError(f"absorbance reply too short: {len(reply)} bytes")
  if reply[6] != _DATA_LAYOUT:
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
  group = [values[k * wells : (k + 1) * wells] for k in range(groups)]
  pairs = [
    (values[groups * wells + 2 * k], values[groups * wells + 2 * k + 1]) for k in range(groups)
  ]
  return group[: len(wavelengths)], group[-1], pairs[: len(wavelengths)], pairs[-1]


def _compute_readings(
  samples: List[List[int]],
  references: List[int],
  calibrations: List[Tuple[int, int]],
  reference_pair: Tuple[int, int],
  report: Report,
) -> List[List[float]]:
  """The readings per wavelength from a decoded data reply (see `_decode_data_reply`).

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


class Absorbance(Measurement):
  """Absorbance measurement.

  Reached as `driver.absorbance`, built at setup only if the plate reader reports it is
  capable of performing transmittance measurements which can be converted to absorbance.
  """

  # The vendor's flashes per well scan where the captures leave them unchanged (A01, A02, B1).
  _VENDOR_FLASHES: Dict[str, int] = {"point": 5, "orbital": 7, "spiral": 15}

  configuration: AbsorbanceConfiguration

  def __init__(
    self, driver: "CLARIOstarDriver", configuration: Optional[AbsorbanceConfiguration] = None
  ):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the absorbance's device facts. Defaults to `AbsorbanceConfiguration()`.
    """
    super().__init__(driver, configuration or AbsorbanceConfiguration())
    self._warned_untested = False

  def _get_run_parameters(
    self,
    wavelengths: Sequence[int],
    report: str,
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
  ) -> Tuple[Plate, List[Well], bytes]:
    """The plate, the wells and the RUN parameters of a measurement, every argument checked.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if the wavelengths or report are out of range (`_check_arguments`), or the
        settings cannot read the wells of the plate on the reader (`_get_plate_and_wells`).
    """
    _check_arguments(wavelengths, report, self.configuration)
    plate, wells, flashes = self._get_plate_and_wells(measurement, shake, wells)
    parameters = _compute_run_parameters(plate, wells, wavelengths, measurement, shake, flashes)
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
      measurement_settings: how to read; None reads with the defaults (the vendor's).
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
      wavelengths, report, measurement, shake_settings, wells
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    # TODO(device): the v1 driver has not run a measurement yet (reader test 7).
    if not self._warned_untested:
      logger.warning(
        "absorbance has not been tested on hardware with this driver; please report how it works"
      )
      self._warned_untested = True
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
    reply, end_timestamp = await _run_measurement(self._driver, parameters, timeout)
    wavelengths = list(wavelengths)
    samples, references, calibrations, reference_pair = _decode_data_reply(reply, wavelengths)
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
