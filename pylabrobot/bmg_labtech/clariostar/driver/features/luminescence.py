"""CLARIOstar luminescence: the light a sample emits, through the emission monochromator.

A basic plate read: the top optic, each well at its centre, enhanced dynamic range on, one
emission band through the monochromator. The focal height, gain, emission band, normalization time,
measurement interval, settling time, reading direction and shaking are sent; the bytes between them
are fixed.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.resources import Plate, Well

from .fluorescence import Fluorescence, _compute_band_edges
from .measurement import (
  Measurement,
  MeasurementConfiguration,
  MeasurementSettings,
  ShakeSettings,
  _get_measured_identifiers,
  _get_readings_by_well,
)

if TYPE_CHECKING:
  from ..configuration import DeviceConfiguration
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)


# ----------------------------------------
# Results and device facts
# ----------------------------------------


@dataclass
class CLARIOstarLuminescenceResult(LuminescenceResult):
  """A CLARIOstar luminescence result, with the detector gain and focal height it was read with.

  Attributes:
    gain: the detector gain, within `LuminescenceConfiguration.gain_range`.
    focal_height: mm above the plate's base; None if not known.
  """

  gain: int
  focal_height: Optional[float] = None

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row shares, with the gain and focal height."""
    return super()._get_base_row(read, gain=self.gain, focal_height=self.focal_height, **columns)


@dataclass
class LuminescenceConfiguration(MeasurementConfiguration):
  """Device facts of the luminescence measurement."""

  wavelength_range: Tuple[int, int] = (320, 840)
  """Emission wavelength, in nm: the emission monochromator's (up to 740 without the extended-red
  photomultiplier on the luminescence path, `Luminescence._get_reader_configuration`)."""
  bandwidth_range: Tuple[int, int] = (8, 100)
  """Emission bandwidth, in nm: the emission monochromator's, as for fluorescence."""
  gain_range: Tuple[int, int] = (0, 4095)
  """Detector gain, as for fluorescence (2000 and 3500 read on hardware)."""
  focal_height_range: Tuple[float, float] = (0.0, 25.0)
  """Focal height, in mm, in 0.1 mm steps (0 to 25, as for fluorescence)."""
  time_range: Tuple[float, float] = (0.01, 655.35)
  """Normalization time and measurement interval, in s, in 0.01 s steps: what their u16 field
  holds. The reader's own limits are not known; 0.20, 0.80, 1.00 and 2.00 s read on hardware."""


class Luminescence(Measurement):
  """Luminescence measurement.

  Reached as `driver.luminescence`, built at setup only if the reader reports a luminescence
  measurement channel.
  """

  configuration: LuminescenceConfiguration

  _MODE = 0x01
  # No `_WELL_SCAN_CODE`: a luminescence read is a point read, without a well-scan field.
  _TRAILER_START = 0x00

  @classmethod
  def _get_reader_configuration(cls, device: "DeviceConfiguration") -> LuminescenceConfiguration:
    """The luminescence facts of the reader `device` describes: its wavelength limits.

    The extended range needs the extended-red photomultiplier on the luminescence path: fitted,
    and on a dual-photomultiplier reader crossed over; otherwise fluorescence's standard range.
    Unknown hardware keeps the defaults.
    """
    configuration = LuminescenceConfiguration()
    standard = device.extended_red_photomultiplier_tube is False or bool(
      device.dual_photomultiplier_tubes and device.photomultiplier_tube_crossover is False
    )
    if standard:
      return replace(configuration, wavelength_range=Fluorescence._STANDARD_WAVELENGTH_RANGE)
    return configuration

  def __init__(
    self, driver: "CLARIOstarDriver", configuration: Optional[LuminescenceConfiguration] = None
  ):
    """Build the luminescence feature of `driver`.

    Args:
      driver: the driver to send commands through.
      configuration: the luminescence's device facts; None for the reader's
        (`_get_reader_configuration`).
    """
    super().__init__(driver, configuration or self._get_reader_configuration(driver.configuration))
    self._default_focal_height_percent_of_well_depth = 25.0

  # ----------------------------------------
  # Defaults
  # ----------------------------------------

  @property
  def default_focal_height_percent_of_well_depth(self) -> float:
    """The focal height of a read given none, in percent of the well depth above its floor."""
    return self._default_focal_height_percent_of_well_depth

  @default_focal_height_percent_of_well_depth.setter
  def default_focal_height_percent_of_well_depth(self, value: float) -> None:
    """Set the default, raising unless it is 0 to 100 %."""
    self._check_default_focal_height_percent(value)
    self._default_focal_height_percent_of_well_depth = value

  def _get_default_focal_height(self, wells: Sequence[Well]) -> float:
    """The focal height `default_focal_height_percent_of_well_depth` up `wells`, mm."""
    return self._compute_default_focal_height(
      wells, self._default_focal_height_percent_of_well_depth, self.configuration.focal_height_range
    )

  # ----------------------------------------
  # Discrete reads
  # ----------------------------------------

  # -- checks --------------------------------------------------------------------------------------

  def _check_arguments(
    self,
    emission_wavelength: int,
    emission_bandwidth: int,
    gain: int,
    focal_height: Optional[float],
    normalization_time: float,
    measurement_interval: float,
    measurement: MeasurementSettings,
    *,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise if the emission band, gain, focal height, times or read settings are out of range.

    The arguments are `measure`'s.

    Raises:
      ValueError: listing every violation: a reader without a monochromator
        (`_check_monochromator`), an emission wavelength not a whole nm within
        `wavelength_range`, `_check_bandwidth`'s, `_check_gain`'s, `_check_focal_height`'s (None
        is the default's, checked with the plate), a normalization time or measurement interval
        not in 0.01 s steps within `time_range`, a well scan other than a point, a pause before
        reading, a timeout not positive.
    """
    errors = self._check_monochromator("luminescence")
    lowest, highest = self.configuration.wavelength_range
    if not isinstance(emission_wavelength, int) or not lowest <= emission_wavelength <= highest:
      errors.append(
        f"emission_wavelength must be a whole {lowest} to {highest} nm, got {emission_wavelength!r}"
      )
    errors += self._check_bandwidth(
      "emission_bandwidth", emission_bandwidth, self.configuration.bandwidth_range
    )
    errors += self._check_gain(gain, self.configuration.gain_range)
    if focal_height is not None:
      errors += self._check_focal_height(focal_height, self.configuration.focal_height_range)
    shortest, longest = self.configuration.time_range
    for name, value in (
      ("normalization_time", normalization_time),
      ("measurement_interval", measurement_interval),
    ):
      if not shortest <= value <= longest or abs(value * 100 - round(value * 100)) > 1e-9:
        errors.append(f"{name} must be {shortest} to {longest} s in 0.01 s steps, got {value}")
    if measurement.well_scan != "point":
      # Only point reads of luminescence are verified.
      errors.append(
        f"luminescence reads each well at its centre (well_scan 'point'), got "
        f"{measurement.well_scan!r}"
      )
    if measurement.pause_before_reading is not None:
      errors.append("a pause before reading is not supported for luminescence")
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    if errors:
      raise ValueError("; ".join(errors))

  # -- RUN parameters ------------------------------------------------------------------------------

  # Between the focal height and the gain, and between the gain and the emission edges; not
  # decoded.
  _AFTER_FOCAL_HEIGHT = bytes.fromhex("0000010000")
  _AFTER_GAIN = bytes.fromhex("000100010001")
  # Between the emission edges and the normalization time; not decoded.
  _AFTER_EMISSION = bytes.fromhex("00050000")
  # Between the normalization time and the run end; not decoded.
  _BEFORE_RUN_END = bytes(4)

  def _compute_run_parameters(
    self,
    plate: Plate,
    wells: List[Well],
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    *,
    emission_wavelength: int,
    emission_bandwidth: int,
    gain: int,
    focal_height: float,
    normalization_time: float,
    measurement_interval: float,
  ) -> bytes:
    """The RUN parameters of a luminescence read.

    The run start (`_compute_run_start`, with enhanced dynamic range), the focal height in
    0.01 mm, the gain u16 BE, the emission band's nominal edges in 0.1 nm (`_compute_band_edges`),
    the normalization time in 0.01 s, then the run end with the measurement interval in 0.01 s
    where an absorbance or fluorescence read has its flashes.
    """
    upper, lower = _compute_band_edges(emission_wavelength, emission_bandwidth)
    return (
      self._compute_run_start(plate, wells, measurement, shake, enhanced_dynamic_range=True)
      + round(focal_height * 100).to_bytes(2, "big")
      + self._AFTER_FOCAL_HEIGHT
      + gain.to_bytes(2, "big")
      + self._AFTER_GAIN
      + upper.to_bytes(2, "big")
      + lower.to_bytes(2, "big")
      + self._AFTER_EMISSION
      + round(normalization_time * 100).to_bytes(2, "big")
      + self._BEFORE_RUN_END
      + self._compute_run_end(round(measurement_interval * 100))
    )

  # -- decoding ------------------------------------------------------------------------------------

  # Data reply layout byte [6], of every luminescence read on hardware.
  _DATA_LAYOUT = 0x25

  def _decode_data_reply(self, reply: bytes, wells: int) -> Tuple[List[int], int, Optional[float]]:
    """The counts row by row, the detector's ceiling, and the temperature.

    - Bytes 7-8, 9-10 and 18-19: the values, one per well.
    - Bytes 11-14: the ceiling a saturated count reads (210,000,000 on hardware).
    - Bytes 32-33: the temperature in 0.1 C, 0 when not monitored.
    - From byte 34: u32 BE counts, one per well.

    Raises:
      ValueError: if the reply is incomplete or not the layout of this read.
    """
    if len(reply) < 34:
      raise ValueError(f"luminescence reply too short: {len(reply)} bytes")
    if reply[6] != self._DATA_LAYOUT:
      raise ValueError(f"luminescence reply layout 0x{reply[6]:02x} is not decoded")
    announced = int.from_bytes(reply[7:9], "big")
    if announced != wells or int.from_bytes(reply[18:20], "big") != wells:
      raise ValueError(f"luminescence reply announces {announced} values; expected {wells} wells")
    if int.from_bytes(reply[9:11], "big") != wells or len(reply) < 34 + 4 * wells:
      raise ValueError("luminescence reply is incomplete")
    counts = [int.from_bytes(reply[34 + 4 * i : 38 + 4 * i], "big") for i in range(wells)]
    raw_temperature = int.from_bytes(reply[32:34], "big")
    temperature = raw_temperature / 10 if raw_temperature else None
    return counts, int.from_bytes(reply[11:15], "big"), temperature

  # -- measuring -----------------------------------------------------------------------------------

  def _get_run_parameters(
    self,
    emission_wavelength: int,
    emission_bandwidth: int,
    gain: int,
    focal_height: Optional[float],
    normalization_time: float,
    measurement_interval: float,
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
    *,
    timeout: Optional[float],
  ) -> Tuple[Plate, List[Well], bytes]:
    """The plate, the wells and the RUN parameters of a read, every argument checked.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if an argument is out of range (`_check_arguments`), or the settings cannot
        read the wells of the plate (`_get_plate_and_wells`).
    """
    self._check_arguments(
      emission_wavelength,
      emission_bandwidth,
      gain,
      focal_height,
      normalization_time,
      measurement_interval,
      measurement,
      timeout=timeout,
    )
    plate, measured = self._get_plate_and_wells(measurement, shake, wells)
    if focal_height is None:
      focal_height = self._get_default_focal_height(measured)
    parameters = self._compute_run_parameters(
      plate,
      measured,
      measurement,
      shake,
      emission_wavelength=emission_wavelength,
      emission_bandwidth=emission_bandwidth,
      gain=gain,
      focal_height=focal_height,
      normalization_time=normalization_time,
      measurement_interval=measurement_interval,
    )
    return plate, measured, parameters

  async def measure(
    self,
    *,
    wells: Optional[List[Well]] = None,
    emission_wavelength: int = 580,
    emission_bandwidth: int = 80,
    gain: int = 3500,
    focal_height: Optional[float] = None,
    normalization_time: float = 1.0,
    measurement_interval: float = 0.8,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    timeout: Optional[float] = None,
    results_directory: Optional[str] = None,
  ) -> CLARIOstarLuminescenceResult:
    """Measure the plate's luminescence through the emission monochromator.

    Reads from the top with enhanced dynamic range on, each well at its centre. Cancelling the call
    stops the run on the reader. No luminescent sample has been read with this driver yet: the
    first read warns.

    Args:
      wells: the wells to measure, on the plate; None measures every well.
      emission_wavelength: whole nm within `configuration.wavelength_range`.
      emission_bandwidth: whole nm within `configuration.bandwidth_range`.
      gain: the detector gain within `configuration.gain_range`.
      focal_height: mm above the plate's base, within `configuration.focal_height_range`, in
        0.1 mm steps. None reads at `default_focal_height_percent_of_well_depth` (25: a quarter
        up the well, for low and moderate fills).
      normalization_time: s the counts are normalized to, in 0.01 s steps within
        `configuration.time_range` (2.00 roughly doubled the counts of 1.00 on hardware).
      measurement_interval: s each well is measured, in 0.01 s steps within
        `configuration.time_range` (at 0.20 a column-1 read, 8 wells, took 9.5 s on hardware
        instead of 14.4 s).
      measurement_settings: how to read: a point read without a pause; its flashes do not apply.
        None reads with the defaults but no settling time.
      shake_settings: shake the plate before the read; None does not shake.
      timeout: seconds the run may take; `default_read_timeout` when None. The defaults read a
        96-well plate in about 105 s.
      results_directory: the directory the read's rows are appended to once it is complete
        (`pylabrobot.lib.results.directory`); None writes none.

    Returns:
      the counts of the measured wells, with the emission band, the gain and the focal height they
      were read with.

    Raises:
      RuntimeError: if no plate is in the plate holder, the run ends without its data, or the last
        write to the results directory failed.
      ValueError: if an argument is out of range (`_check_arguments`), the settings cannot read
        the wells of the plate, the reply is not this read's (`_decode_data_reply`), or the results
        directory cannot be created or written.
      TimeoutError: if the run takes longer than `timeout` seconds.
    """
    measurement = (
      MeasurementSettings(settling_time=0.0)
      if measurement_settings is None
      else measurement_settings
    )
    plate, wells, parameters = self._get_run_parameters(
      emission_wavelength,
      emission_bandwidth,
      gain,
      focal_height,
      normalization_time,
      measurement_interval,
      measurement,
      shake_settings,
      wells,
      timeout=timeout,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    if focal_height is None:
      focal_height = self._get_default_focal_height(wells)
    # TODO(device): no luminescent sample has been read yet, only an empty plate.
    self._warn_untested("luminescence")
    logger.info(
      "luminescence: %d/%d nm, gain %d, %.2f s per well, %d of %d wells of %s",
      emission_wavelength,
      emission_bandwidth,
      gain,
      measurement_interval,
      len(wells),
      plate.num_items,
      plate.name,
    )
    results_directory = self._check_results_directory(results_directory)
    start_timestamp = datetime.now()
    replies, end_timestamp = await self._run_measurement(parameters, timeout)
    identifiers = _get_measured_identifiers(plate, wells)
    counts, ceiling, temperature = self._decode_data_reply(replies[0], len(identifiers))
    self._warn_saturated("luminescence", counts, ceiling)
    result = CLARIOstarLuminescenceResult(
      data=_get_readings_by_well(plate, wells, [float(count) for count in counts]),
      emission_wavelength=emission_wavelength,
      emission_bandwidth=emission_bandwidth,
      gain=gain,
      focal_height=focal_height,
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )
    await self._append_results(results_directory, [result])
    return result
