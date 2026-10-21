"""Fluorescence: intensity at one excitation and one emission wavelength, read from the top,
each side through its monochromator or a filter."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Tuple

from pylabrobot.lib.fluorescence.results import FluorescenceResult
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.resources import Plate, Well

from .measurement import (
  MODE_FLUORESCENCE,
  Measurement,
  MeasurementConfiguration,
  MeasurementSettings,
  ShakeSettings,
  _compute_run_end,
  _compute_run_start,
  _get_measured_identifiers,
  _get_readings_by_well,
)

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

# Fluorescence in the well-scan field (F-G01, F-G02).
_WELL_SCAN_CODE = 0x03
# After the focal height: the excitation/emission pair count at byte 2, one pair (F-A01).
_PAIR_HEADER = bytes.fromhex("00000100000000000c")
# Bandwidths through the monochromators when none is given, in nm (F-A01).
_DEFAULT_EXCITATION_BANDWIDTH = 15
_DEFAULT_EMISSION_BANDWIDTH = 20
# The light paths after the emission edges, emission then excitation: its monochromator, or a
# filter (F-A01, F-Lf01 to F-Lf03).
_EMISSION_MONOCHROMATOR = 0x04
_EXCITATION_MONOCHROMATOR = 0x03
_FILTER = 0x01
# Pause before reading: off. No fluorescence read with a pause is captured.
_NO_PAUSE = bytes(3)
_TRAILER_START = 0x00

# Data reply layout byte [6], with the temperature controller on or off (F-A01, F-K01).
_DATA_LAYOUT = 0xA1


@dataclass
class CLARIOstarFluorescenceResult(FluorescenceResult):
  """A CLARIOstar fluorescence result, with the detector gain the read used.

  Attributes:
    gain: the detector gain, within `FluorescenceConfiguration.gain_range`.
  """

  gain: int


@dataclass
class FluorescenceConfiguration(MeasurementConfiguration):
  """Device facts of the fluorescence measurement."""

  wavelength_range: Tuple[int, int] = (320, 840)
  """Excitation and emission wavelengths, in nm (the operating manual's monochromator range)."""
  bandwidth_range: Tuple[int, int] = (8, 100)
  """Excitation and emission bandwidths, in nm (the operating manual)."""
  dichroic_range: Tuple[float, float] = (340.0, 760.0)
  """Where the dichroic mirror splits excitation from emission, in nm (the operating manual)."""
  gain_range: Tuple[int, int] = (0, 4095)
  """Detector gain (the vendor software manual: 0 to 4095)."""
  focal_height_range: Tuple[float, float] = (0.0, 25.0)
  """Focal height, in mm, in 0.1 mm steps (the vendor software manual: up to 25 mm)."""
  excitation_filter_slots: int = 4
  """Excitation filter positions (the vendor software manual: up to 4)."""
  dichroic_filter_slots: int = 3
  """Dichroic filter positions (the vendor software manual: up to 3)."""
  emission_filter_slots: int = 4
  """Emission filter positions (the vendor software manual: up to 4)."""
  filter_slots_per_slide: int = 2
  """Excitation or emission filter positions on each of that side's two slides (the filter
  detection capture)."""


def _compute_band_edges(wavelength: int, bandwidth: int) -> Tuple[int, int]:
  """The band's upper and lower edge in 0.1 nm: `wavelength` +/- half `bandwidth`."""
  return round((wavelength + bandwidth / 2) * 10), round((wavelength - bandwidth / 2) * 10)


def _compute_filter_positions(slot: int, per_slide: int) -> Tuple[int, int]:
  """The motor positions of one side's two slides for filter `slot`: the first slide's, then the
  second's. 1 is a slide's home, 2 its first filter (the filter detection capture, F-Lf01)."""
  if slot <= per_slide:
    return slot + 1, 1
  return 1, slot - per_slide + 1


def _check_side(
  name: str,
  wavelength: Optional[int],
  bandwidth: Optional[int],
  optical_filter: Optional[OpticalFilter],
  slots: int,
  configuration: "FluorescenceConfiguration",
) -> List[str]:
  """The violations of one side: through its monochromator, a wavelength and bandwidth within
  range; through a filter, a slot within range and no wavelength or bandwidth."""
  if optical_filter is not None:
    errors = []
    if wavelength is not None or bandwidth is not None:
      errors.append(f"{name}_filter replaces {name}_wavelength and {name}_bandwidth")
    if not isinstance(optical_filter.slot, int) or not 1 <= optical_filter.slot <= slots:
      errors.append(f"{name}_filter slot must be 1 to {slots}, got {optical_filter.slot!r}")
    return errors
  if wavelength is None:
    return [f"give {name}_wavelength or {name}_filter"]
  errors = []
  lowest, highest = configuration.wavelength_range
  if not isinstance(wavelength, int) or not lowest <= wavelength <= highest:
    errors.append(f"{name}_wavelength must be a whole {lowest} to {highest} nm, got {wavelength!r}")
  narrowest, widest = configuration.bandwidth_range
  if bandwidth is not None and (
    not isinstance(bandwidth, int) or not narrowest <= bandwidth <= widest
  ):
    errors.append(f"{name}_bandwidth must be a whole {narrowest} to {widest} nm, got {bandwidth!r}")
  return errors


def _check_arguments(
  excitation_wavelength: Optional[int],
  excitation_bandwidth: Optional[int],
  excitation_filter: Optional[OpticalFilter],
  emission_wavelength: Optional[int],
  emission_bandwidth: Optional[int],
  emission_filter: Optional[OpticalFilter],
  dichroic_filter: Optional[DichroicFilter],
  dichroic_split: Optional[float],
  gain: int,
  focal_height: float,
  measurement: MeasurementSettings,
  configuration: FluorescenceConfiguration,
  timeout: Optional[float] = None,
) -> None:
  """Raise if the optics, gain, focal height, pause or timeout are out of range.

  Raises:
    ValueError: listing every violation: a side with both or neither of a wavelength and a
      filter (`_check_side`), a filter with no fixed dichroic, both a fixed dichroic and a split,
      a dichroic slot out of range, the excitation band not below the emission band, a split not
      in 0.1 nm steps within `dichroic_range` and between the wavelengths, a gain not whole
      within `gain_range`, a focal height not in 0.1 mm steps within `focal_height_range`, a
      pause before reading, a timeout not positive.
  """
  errors = _check_side(
    "excitation",
    excitation_wavelength,
    excitation_bandwidth,
    excitation_filter,
    configuration.excitation_filter_slots,
    configuration,
  ) + _check_side(
    "emission",
    emission_wavelength,
    emission_bandwidth,
    emission_filter,
    configuration.emission_filter_slots,
    configuration,
  )
  filtered = excitation_filter is not None or emission_filter is not None
  if dichroic_filter is not None:
    slots = configuration.dichroic_filter_slots
    if dichroic_split is not None:
      errors.append("dichroic_filter and dichroic_split exclude each other")
    if not isinstance(dichroic_filter.slot, int) or not 1 <= dichroic_filter.slot <= slots:
      errors.append(f"dichroic_filter slot must be 1 to {slots}, got {dichroic_filter.slot!r}")
  elif filtered:
    # No read with a filter on either side and the dichroic mirror is captured.
    errors.append("a read with a filter needs a dichroic_filter")
  if not errors and excitation_wavelength is not None and emission_wavelength is not None:
    excitation_upper, _ = _compute_band_edges(
      excitation_wavelength, excitation_bandwidth or _DEFAULT_EXCITATION_BANDWIDTH
    )
    _, emission_lower = _compute_band_edges(
      emission_wavelength, emission_bandwidth or _DEFAULT_EMISSION_BANDWIDTH
    )
    if excitation_upper >= emission_lower:
      errors.append(
        f"the excitation band ({excitation_upper / 10} nm at the top) must lie below the emission "
        f"band ({emission_lower / 10} nm at the bottom)"
      )
    elif dichroic_filter is None:
      if dichroic_split is None:
        split = round((excitation_upper + emission_lower) / 2) / 10
      else:
        split = dichroic_split
        if not excitation_wavelength < split < emission_wavelength or (
          abs(split * 10 - round(split * 10)) > 1e-9
        ):
          errors.append(
            f"dichroic_split must lie between {excitation_wavelength} and {emission_wavelength} "
            f"nm in 0.1 nm steps, got {split}"
          )
      lowest_split, highest_split = configuration.dichroic_range
      if not lowest_split <= split <= highest_split:
        errors.append(f"the dichroic split {split} nm must be {lowest_split} to {highest_split} nm")
  lowest_gain, highest_gain = configuration.gain_range
  if not isinstance(gain, int) or not lowest_gain <= gain <= highest_gain:
    errors.append(f"gain must be a whole {lowest_gain} to {highest_gain}, got {gain!r}")
  lowest_height, highest_height = configuration.focal_height_range
  if (
    not lowest_height <= focal_height <= highest_height
    or abs(focal_height * 10 - round(focal_height * 10)) > 1e-9
  ):
    errors.append(
      f"focal_height must be {lowest_height} to {highest_height} mm in 0.1 mm steps, got "
      f"{focal_height}"
    )
  if measurement.pause_before_reading is not None:
    errors.append("a pause before reading is not supported for fluorescence")
  if timeout is not None and not timeout > 0:
    errors.append(f"timeout must be positive, got {timeout}")
  if errors:
    raise ValueError("; ".join(errors))


def _compute_run_parameters(
  plate: Plate,
  wells: List[Well],
  excitation_wavelength: Optional[int],
  excitation_bandwidth: Optional[int],
  excitation_filter: Optional[OpticalFilter],
  emission_wavelength: Optional[int],
  emission_bandwidth: Optional[int],
  emission_filter: Optional[OpticalFilter],
  dichroic_filter: Optional[DichroicFilter],
  dichroic_split: Optional[float],
  gain: int,
  focal_height: float,
  measurement: MeasurementSettings,
  shake: Optional[ShakeSettings],
  per_slide: int,
) -> bytes:
  """The RUN parameters of a fluorescence read: focal height in 0.01 mm, one excitation/emission
  pair, no pause (F-A01 to F-L01, F-Lf01 to F-Lf03).

  The pair is the gain, the excitation fields, the dichroic, the emission fields and the two light
  paths. Through a monochromator a side's fields are its band edges in 0.1 nm; through a filter
  they are the motor positions of its two slides (`_compute_filter_positions`), emission's in
  reverse order. The dichroic is a fixed one's slot + 1, a split in 0.1 nm, or by default the
  midpoint of the excitation band's top and the emission band's bottom, rounded half to even.
  """
  if excitation_filter is not None:
    excitation = _compute_filter_positions(excitation_filter.slot, per_slide)
  elif excitation_wavelength is not None:
    excitation = _compute_band_edges(
      excitation_wavelength, excitation_bandwidth or _DEFAULT_EXCITATION_BANDWIDTH
    )
  else:
    raise ValueError("give excitation_wavelength or excitation_filter")
  if emission_filter is not None:
    first, second = _compute_filter_positions(emission_filter.slot, per_slide)
    emission = (second, first)
  elif emission_wavelength is not None:
    emission = _compute_band_edges(
      emission_wavelength, emission_bandwidth or _DEFAULT_EMISSION_BANDWIDTH
    )
  else:
    raise ValueError("give emission_wavelength or emission_filter")
  if dichroic_filter is not None:
    dichroic = dichroic_filter.slot + 1
  elif dichroic_split is not None:
    dichroic = round(dichroic_split * 10)
  else:
    dichroic = round((excitation[0] + emission[1]) / 2)
  pair = b"".join(value.to_bytes(2, "big") for value in (gain, *excitation, dichroic, *emission))
  paths = bytes(
    [
      0,
      _EMISSION_MONOCHROMATOR if emission_filter is None else _FILTER,
      0,
      _EXCITATION_MONOCHROMATOR if excitation_filter is None else _FILTER,
      0,
    ]
  )
  return (
    _compute_run_start(plate, wells, MODE_FLUORESCENCE, _WELL_SCAN_CODE, measurement, shake)
    + round(focal_height * 100).to_bytes(2, "big")
    + _PAIR_HEADER
    + pair
    + paths
    + _NO_PAUSE
    + _compute_run_end(_TRAILER_START, measurement.flashes)
  )


def _describe_side(
  wavelength: Optional[int], bandwidth: Optional[int], optical_filter: Optional[OpticalFilter]
) -> str:
  """One side of the read, for the log: its wavelength and bandwidth, or its filter."""
  if optical_filter is not None:
    return f"filter {optical_filter.slot}" + (
      f" ({optical_filter.name})" if optical_filter.name else ""
    )
  return f"{wavelength}/{bandwidth} nm"


def _decode_data_reply(reply: bytes, wells: int) -> Tuple[List[int], int, Optional[float]]:
  """The counts in the reply's well order (row by row), the detector's ceiling, and the
  temperature.

  The reply holds one u32 per well from byte 34; bytes 11-14 are the ceiling a saturated well
  reads, bytes 32-33 the temperature in 0.1 C, 0 when the temperature controller is not
  monitoring (F-A01, F-D02, F-K01).

  Raises:
    ValueError: if the reply is incomplete or not the layout of this read.
  """
  if len(reply) < 34:
    raise ValueError(f"fluorescence reply too short: {len(reply)} bytes")
  if reply[6] != _DATA_LAYOUT:
    raise ValueError(f"fluorescence reply layout 0x{reply[6]:02x} is not decoded")
  announced = int.from_bytes(reply[7:9], "big")
  if announced != wells or int.from_bytes(reply[18:20], "big") != wells:
    raise ValueError(f"fluorescence reply announces {announced} values; expected {wells}")
  if int.from_bytes(reply[9:11], "big") != wells or len(reply) < 34 + 4 * wells:
    raise ValueError("fluorescence reply is incomplete")
  counts = [int.from_bytes(reply[34 + 4 * i : 38 + 4 * i], "big") for i in range(wells)]
  raw_temperature = int.from_bytes(reply[32:34], "big")
  temperature = raw_temperature / 10 if raw_temperature else None
  return counts, int.from_bytes(reply[11:15], "big"), temperature


class Fluorescence(Measurement):
  """Fluorescence measurement.

  Reached as `driver.fluorescence`, built at setup only if the reader reports a fluorescence
  channel.
  """

  configuration: FluorescenceConfiguration

  def __init__(
    self, driver: "CLARIOstarDriver", configuration: Optional[FluorescenceConfiguration] = None
  ):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the fluorescence's device facts. Defaults to `FluorescenceConfiguration()`.
    """
    super().__init__(driver, configuration or FluorescenceConfiguration())

  async def measure(
    self,
    *,
    wells: Optional[List[Well]] = None,
    excitation_wavelength: Optional[int] = None,
    emission_wavelength: Optional[int] = None,
    excitation_bandwidth: Optional[int] = None,
    emission_bandwidth: Optional[int] = None,
    excitation_filter: Optional[OpticalFilter] = None,
    emission_filter: Optional[OpticalFilter] = None,
    dichroic_filter: Optional[DichroicFilter] = None,
    dichroic_split: Optional[float] = None,
    gain: int,
    focal_height: float = 8.5,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    timeout: Optional[float] = None,
  ) -> CLARIOstarFluorescenceResult:
    """Measure the plate in the plate holder at one excitation and emission wavelength.

    Each side goes through its monochromator (a wavelength) or a filter. Cancelling the call
    stops the run on the reader.

    Args:
      wells: the wells to measure, on the plate; None measures every well.
      excitation_wavelength: through the monochromator, whole nm within
        `configuration.wavelength_range`.
      emission_wavelength: through the monochromator, whole nm within
        `configuration.wavelength_range`, its band above the excitation band.
      excitation_bandwidth: whole nm within `configuration.bandwidth_range`; None is 15 (F-A01).
      emission_bandwidth: whole nm within `configuration.bandwidth_range`; None is 20 (F-A01).
      excitation_filter: the excitation filter instead of the monochromator; its slot within
        `configuration.excitation_filter_slots`.
      emission_filter: the emission filter instead of the monochromator; its slot within
        `configuration.emission_filter_slots`.
      dichroic_filter: a fixed dichroic, its slot within `configuration.dichroic_filter_slots`;
        needed when either side uses a filter.
      dichroic_split: where the dichroic mirror splits, nm in 0.1 nm steps between the two
        wavelengths (F-L01); None splits midway between the bands.
      gain: detector gain within `configuration.gain_range`; too high a gain saturates wells.
      focal_height: mm within `configuration.focal_height_range`, in 0.1 mm steps (F-A01: 8.5).
      measurement_settings: how to read, without a pause; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      timeout: seconds the run may take; `default_read_timeout` when None.

    Returns:
      the counts of the measured wells, with the optics and the gain they were read with.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without its data.
      ValueError: if an argument is out of range (`_check_arguments`), or the settings cannot read
        the wells of the plate.
      TimeoutError: if the run takes longer than `timeout` seconds.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    optics = (
      excitation_wavelength,
      excitation_bandwidth,
      excitation_filter,
      emission_wavelength,
      emission_bandwidth,
      emission_filter,
      dichroic_filter,
      dichroic_split,
    )
    _check_arguments(*optics, gain, focal_height, measurement, self.configuration, timeout)
    plate, wells = self._get_plate_and_wells(measurement, shake_settings, wells)
    parameters = _compute_run_parameters(
      plate,
      wells,
      *optics,
      gain,
      focal_height,
      measurement,
      shake_settings,
      self.configuration.filter_slots_per_slide,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    self._warn_untested("fluorescence")
    logger.info(
      "fluorescence: %s -> %s, gain %d, %d of %d wells of %s, %s scan",
      _describe_side(
        excitation_wavelength,
        excitation_bandwidth or _DEFAULT_EXCITATION_BANDWIDTH,
        excitation_filter,
      ),
      _describe_side(
        emission_wavelength, emission_bandwidth or _DEFAULT_EMISSION_BANDWIDTH, emission_filter
      ),
      gain,
      len(wells),
      plate.num_items,
      plate.name,
      measurement.well_scan,
    )
    start_timestamp = datetime.now()
    replies, end_timestamp = await self._run_measurement(parameters, timeout)
    measured = len(_get_measured_identifiers(plate, wells))
    counts, ceiling, temperature = _decode_data_reply(replies[0], measured)
    saturated = sum(count >= ceiling for count in counts)
    if saturated:
      logger.warning(
        "fluorescence: %d of %d wells read the detector's ceiling (%d); lower the gain",
        saturated,
        len(counts),
        ceiling,
      )
    return CLARIOstarFluorescenceResult(
      data=_get_readings_by_well(plate, wells, [float(count) for count in counts]),
      excitation_wavelength=(
        excitation_filter.center_wavelength
        if excitation_filter is not None
        else excitation_wavelength
      ),
      excitation_bandwidth=(
        excitation_filter.bandwidth
        if excitation_filter is not None
        else excitation_bandwidth or _DEFAULT_EXCITATION_BANDWIDTH
      ),
      emission_wavelength=(
        emission_filter.center_wavelength if emission_filter is not None else emission_wavelength
      ),
      emission_bandwidth=(
        emission_filter.bandwidth
        if emission_filter is not None
        else emission_bandwidth or _DEFAULT_EMISSION_BANDWIDTH
      ),
      gain=gain,
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )
