"""Top fluorescence intensity: endpoint and spectrum reads through the monochromators, the
automatic gain and focus, and z-profiles.

A read goes through one or more fluorescence channels (``FluorescenceChannel``,
``pylabrobot.lib.fluorescence``), each an excitation and an emission wavelength and bandwidth.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union, overload

from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.resources import Plate, Well

from ..responses import GainFocus, MeasurementData
from .measurement import (
  Measurement,
  MeasurementSettings,
  MonochromatorSettings,
  ScanSide,
  ShakeSettings,
  _get_identifier,
  _get_measured_wells,
  compute_monochromator_settings,
  compute_scan_monochromators,
  get_plate_geometry,
)

if TYPE_CHECKING:
  from ..master import VANTAstarDriver

logger = logging.getLogger(__name__)

# Monochromator limits of BMG's filter software, in nm: the bandwidths it allows, the shortest
# excitation wavelength and lower excitation edge, and the longest wavelength (with the extended
# infrared detector; without it Voyager stops at 740 nm).
MINIMUM_BANDWIDTH = 8.0
MAXIMUM_BANDWIDTH = 100.0
MINIMUM_EXCITATION = 320.0
MINIMUM_EXCITATION_EDGE = 310.0
MAXIMUM_WAVELENGTH = 840.0
# Fluorescence channels in one read: the reader refuses a sixth (quit code 60).
MAXIMUM_FLUORESCENCE_CHANNELS = 5
# Flashes and settling time when the measurement settings give none.
_DEFAULT_FLASHES = 20
_DEFAULT_SETTLING_TIME = 0.5

# A channel's optics: excitation wavelength and bandwidth, emission wavelength and bandwidth, and
# the dichroic split (None for the automatic one).
_Optics = Tuple[float, float, float, float, Optional[float]]
# The steps between a z-profile's points the API takes, mm.
_Z_PROFILE_STEP_RANGE = (0.1, 1.0)


@dataclass(frozen=True)
class VANTAstarZProfileResult:
  """The z-scan of one well as the reader reports it (``Fluorescence.measure_z_profile``).

  Attributes:
    well: the identifier of the scanned well, as the reader names it (``"A1"``).
    gain: the detector gain the reader fits at the peak for the target; None without a target.
    z_profile: (focal height in mm, counts) from the highest to the lowest point; the counts at a
      gain the reader chooses for the scan: for the shape, not comparable across scans.
    average_signal: the reader's smoothed z-profile at ``peak_focal_height``, counts: the average
      of the five points around it when the peak falls on a point.
    peak_counts: the counts at ``gain``; None without a target.
    peak_detector_percent: ``peak_counts`` in percent of the detector's 260,000 counts, as the
      target; None without a target.
    peak_focal_height: the focal height the reader computes as the z-profile's peak, in mm.
    start_timestamp: when the z-scan started.
    end_timestamp: when its result was in.
  """

  well: str
  gain: Optional[int]
  z_profile: List[Tuple[float, int]]
  average_signal: int
  peak_counts: Optional[int]
  peak_detector_percent: Optional[float]
  peak_focal_height: float
  start_timestamp: datetime
  end_timestamp: datetime


class Fluorescence(Measurement):
  """Top fluorescence intensity measurement (RUN, MeasurementMode 0), and the gain and focus
  adjustment and z-profiles (GainPlateCommand).

  Reached as ``driver.fluorescence``.
  """

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    super().__init__(driver)
    self._default_excitation_bandwidth = 8.0  # nm
    self._default_emission_bandwidth = 12.0  # nm
    self._default_focal_height_percent_of_well_depth = 25.0

  # -- defaults ------------------------------------------------------------------------------------

  @staticmethod
  def _check_bandwidth(name: str, value: float) -> None:
    """Raise unless ``value`` is a bandwidth the monochromators take (8-100 nm)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
      raise ValueError(f"{name} must be a number of nm, got {value!r}")
    if not MINIMUM_BANDWIDTH <= value <= MAXIMUM_BANDWIDTH:
      raise ValueError(f"{name} must be {MINIMUM_BANDWIDTH}-{MAXIMUM_BANDWIDTH} nm, got {value}")

  @property
  def default_excitation_bandwidth(self) -> float:
    """The excitation bandwidth of a channel or spectrum that gives none, nm (8)."""
    return self._default_excitation_bandwidth

  @default_excitation_bandwidth.setter
  def default_excitation_bandwidth(self, value: float) -> None:
    """Set the default, raising if ``_check_bandwidth`` refuses it."""
    self._check_bandwidth("default_excitation_bandwidth", value)
    self._default_excitation_bandwidth = value

  @property
  def default_emission_bandwidth(self) -> float:
    """The emission bandwidth of a channel or spectrum that gives none, nm (12)."""
    return self._default_emission_bandwidth

  @default_emission_bandwidth.setter
  def default_emission_bandwidth(self, value: float) -> None:
    """Set the default, raising if ``_check_bandwidth`` refuses it."""
    self._check_bandwidth("default_emission_bandwidth", value)
    self._default_emission_bandwidth = value

  @property
  def default_focal_height_percent_of_well_depth(self) -> float:
    """The focal height a read takes when none is given: this percent of the wells' depth above
    their floor (25 %, as on the CLARIOstar; 6.2 mm on a Thermo SureSTART 96-well plate, where the
    reader's autofocus found 6.2-6.6 mm)."""
    return self._default_focal_height_percent_of_well_depth

  @default_focal_height_percent_of_well_depth.setter
  def default_focal_height_percent_of_well_depth(self, value: float) -> None:
    """Set the default, raising unless it is 0 to 100 %."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
      raise ValueError(
        f"default_focal_height_percent_of_well_depth must be 0 to 100 %, got {value!r}"
      )
    self._default_focal_height_percent_of_well_depth = value

  def _get_focal_height(
    self, focal_height: Optional[float], plate: Plate, wells: Optional[Sequence[Well]]
  ) -> float:
    """``focal_height``, or the default for the wells read (every well of ``plate`` when None)."""
    if focal_height is not None:
      return focal_height
    return self._compute_default_focal_height(
      plate.get_all_items() if wells is None else wells,
      self._default_focal_height_percent_of_well_depth,
    )

  def _get_fluorescence_settings(
    self, measurement_settings: Optional[MeasurementSettings], plate: Plate
  ) -> MeasurementSettings:
    """``measurement_settings`` with fluorescence's default flashes and settling time filled in."""
    return self._get_settings(
      measurement_settings, plate, flashes=_DEFAULT_FLASHES, settling_time=_DEFAULT_SETTLING_TIME
    )

  # -- optics --------------------------------------------------------------------------------------

  @staticmethod
  def _check_optics(
    excitation_wavelength: float,
    excitation_bandwidth: float,
    emission_wavelength: float,
    emission_bandwidth: float,
  ) -> None:
    """Raise unless excitation and emission are within the limits BMG's filter software sets.

    Raises:
      ValueError: listing every violation.
    """
    errors = []
    for name, bandwidth in (
      ("excitation_bandwidth", excitation_bandwidth),
      ("emission_bandwidth", emission_bandwidth),
    ):
      if not MINIMUM_BANDWIDTH <= bandwidth <= MAXIMUM_BANDWIDTH:
        errors.append(f"{name} must be {MINIMUM_BANDWIDTH}-{MAXIMUM_BANDWIDTH} nm, got {bandwidth}")
    excitation_lower = excitation_wavelength - excitation_bandwidth / 2
    excitation_upper = excitation_wavelength + excitation_bandwidth / 2
    if excitation_wavelength < MINIMUM_EXCITATION or excitation_lower < MINIMUM_EXCITATION_EDGE:
      errors.append(
        f"excitation must be at least {MINIMUM_EXCITATION} nm with its lower edge at least "
        f"{MINIMUM_EXCITATION_EDGE} nm, got {excitation_wavelength}/{excitation_bandwidth}"
      )
    if max(excitation_wavelength, emission_wavelength) > MAXIMUM_WAVELENGTH:
      errors.append(f"wavelengths must be at most {MAXIMUM_WAVELENGTH} nm")
    if emission_wavelength - emission_bandwidth / 2 < excitation_upper:
      errors.append(
        f"emission {emission_wavelength}/{emission_bandwidth} overlaps excitation "
        f"{excitation_wavelength}/{excitation_bandwidth}"
      )
    if errors:
      raise ValueError("; ".join(errors))

  def _get_optics(self, channel: FluorescenceChannel) -> _Optics:
    """A channel's optics, its bandwidths the defaults where it gives none, checked.

    Raises:
      ValueError: if the channel names a filter, lacks a wavelength, or its optics are out of the
        limits (``_check_optics``).
    """
    if (
      channel.excitation_filter is not None
      or channel.emission_filter is not None
      or channel.dichroic_filter is not None
    ):
      raise ValueError("the VANTAstar reads through its monochromators; a channel takes no filters")
    if channel.excitation_wavelength is None or channel.emission_wavelength is None:
      raise ValueError("a fluorescence channel needs an excitation and an emission wavelength")
    excitation_bandwidth = (
      self._default_excitation_bandwidth
      if channel.excitation_bandwidth is None
      else channel.excitation_bandwidth
    )
    emission_bandwidth = (
      self._default_emission_bandwidth
      if channel.emission_bandwidth is None
      else channel.emission_bandwidth
    )
    self._check_optics(
      channel.excitation_wavelength,
      excitation_bandwidth,
      channel.emission_wavelength,
      emission_bandwidth,
    )
    return (
      channel.excitation_wavelength,
      excitation_bandwidth,
      channel.emission_wavelength,
      emission_bandwidth,
      channel.dichroic_split_wavelength,
    )

  @staticmethod
  def _get_list(
    value: Union[object, Sequence[object], None], count: int, name: str
  ) -> List[object]:
    """``value`` once per channel: a sequence of ``count``, or one value repeated.

    Raises:
      ValueError: if a sequence does not hold ``count`` values.
    """
    if isinstance(value, (list, tuple)):
      if len(value) != count:
        raise ValueError(f"{len(value)} {name} for {count} fluorescence channels")
      return list(value)
    return [value] * count

  def _build_run(
    self,
    optics: Sequence[_Optics],
    *,
    gains: Sequence[Optional[int]],
    monochromators: Sequence[Optional[MonochromatorSettings]],
    focal_height: float,
    enhanced_dynamic_range: bool,
    plate: Plate,
    wells: Optional[Sequence[Well]],
    settings: MeasurementSettings,
  ) -> bytes:
    """The RUN fields of a fluorescence intensity read through the channels' ``optics``.

    Raises:
      ValueError: if a gain is missing without enhanced dynamic range.
    """
    if not enhanced_dynamic_range and any(gain is None for gain in gains):
      raise ValueError("a gain is required unless enhanced_dynamic_range")
    settings_per_channel = [
      monochromator
      if monochromator is not None
      else compute_monochromator_settings(
        excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic
      )
      for (
        excitation,
        excitation_bandwidth,
        emission,
        emission_bandwidth,
        dichroic,
      ), monochromator in zip(optics, monochromators)
    ]
    return self._driver.build_fluorescence_run(
      settings_per_channel,
      gains=[gain or 0 for gain in gains],
      focal_height=focal_height,
      enhanced_dynamic_range=enhanced_dynamic_range,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      **self._get_run_fields(settings),
    )

  def _get_results(
    self,
    data: MeasurementData,
    measured: List[Tuple[int, int]],
    optics: Sequence[_Optics],
    start: datetime,
    end: datetime,
  ) -> List[FluorescenceResult]:
    """One ``FluorescenceResult`` per channel of a fluorescence block; warns of saturated wells."""
    self._check_wells(data, measured)
    results = []
    for k, (excitation, excitation_bandwidth, emission, emission_bandwidth, _) in enumerate(optics):
      counts = data.group(k)
      saturated = sum(1 for count in counts if count >= data.overflow)
      if saturated:
        logger.warning(
          "%d of %d wells read at the reader's ceiling of %d counts",
          saturated,
          len(counts),
          data.overflow,
        )
      results.append(
        FluorescenceResult(
          data={_get_identifier(r, c): float(v) for (r, c), v in zip(measured, counts)},
          excitation_wavelength=excitation,
          excitation_bandwidth=excitation_bandwidth,
          emission_wavelength=emission,
          emission_bandwidth=emission_bandwidth,
          temperature=data.temperature,
          start_timestamp=start,
          end_timestamp=end,
        )
      )
    return results

  def _prepare_read(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    gain: Union[int, Sequence[int], None],
    monochromator: Union[MonochromatorSettings, Sequence[MonochromatorSettings], None],
  ) -> Tuple[List[_Optics], List[Optional[int]], List[Optional[MonochromatorSettings]]]:
    """The channels' optics, a gain and a monochromator override (or None) per channel.

    Raises:
      ValueError: if there is no channel, or a channel, the gains or the overrides are invalid.
    """
    channels = (
      [fluorescence_channels]
      if isinstance(fluorescence_channels, FluorescenceChannel)
      else list(fluorescence_channels)
    )
    if not 1 <= len(channels) <= MAXIMUM_FLUORESCENCE_CHANNELS:
      raise ValueError(
        f"give 1 to {MAXIMUM_FLUORESCENCE_CHANNELS} fluorescence channels, got {len(channels)}"
      )
    optics = [self._get_optics(channel) for channel in channels]
    gains = self._get_list(gain, len(channels), "gains")
    monochromators = self._get_list(monochromator, len(channels), "monochromators")
    return optics, gains, monochromators  # type: ignore[return-value]

  # -- reads ---------------------------------------------------------------------------------------

  def _build_measure(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    gain: Union[int, Sequence[int], None],
    monochromator: Union[MonochromatorSettings, Sequence[MonochromatorSettings], None],
    focal_height: Optional[float],
    enhanced_dynamic_range: bool,
    plate: Optional[Plate],
    wells: Optional[Sequence[Well]],
    measurement_settings: Optional[MeasurementSettings],
  ) -> Tuple[Plate, MeasurementSettings, List[Tuple[int, int]], List[_Optics], bytes]:
    """Check an endpoint read and build its RUN fields: the plate, the settings, the measured
    wells (row, column), each channel's optics and the fields.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if an argument is out of range.
    """
    optics, gains, monochromators = self._prepare_read(fluorescence_channels, gain, monochromator)
    plate = self._get_plate(plate)
    settings = self._get_fluorescence_settings(measurement_settings, plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._build_run(
      optics,
      gains=gains,
      monochromators=monochromators,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      enhanced_dynamic_range=enhanced_dynamic_range,
      plate=plate,
      wells=wells,
      settings=settings,
    )
    return plate, settings, measured, optics, parameters

  @overload
  async def measure(
    self,
    fluorescence_channels: FluorescenceChannel,
    *,
    gain: Optional[int] = ...,
    wells: Optional[Sequence[Well]] = ...,
    focal_height: Optional[float] = ...,
    enhanced_dynamic_range: bool = ...,
    monochromator: Optional[MonochromatorSettings] = ...,
    plate: Optional[Plate] = ...,
    measurement_settings: Optional[MeasurementSettings] = ...,
    shake_settings: Optional[ShakeSettings] = ...,
    simulated_data: Optional[Sequence[FluorescenceResult]] = ...,
    timeout: float = ...,
  ) -> FluorescenceResult: ...

  @overload
  async def measure(
    self,
    fluorescence_channels: Sequence[FluorescenceChannel],
    *,
    gain: Union[int, Sequence[int], None] = ...,
    wells: Optional[Sequence[Well]] = ...,
    focal_height: Optional[float] = ...,
    enhanced_dynamic_range: bool = ...,
    monochromator: Optional[Sequence[MonochromatorSettings]] = ...,
    plate: Optional[Plate] = ...,
    measurement_settings: Optional[MeasurementSettings] = ...,
    shake_settings: Optional[ShakeSettings] = ...,
    simulated_data: Optional[Sequence[FluorescenceResult]] = ...,
    timeout: float = ...,
  ) -> List[FluorescenceResult]: ...

  async def measure(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    *,
    gain: Union[int, Sequence[int], None] = None,
    wells: Optional[Sequence[Well]] = None,
    focal_height: Optional[float] = None,
    enhanced_dynamic_range: bool = False,
    monochromator: Union[MonochromatorSettings, Sequence[MonochromatorSettings], None] = None,
    plate: Optional[Plate] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[Sequence[FluorescenceResult]] = None,
    timeout: float = 300.0,
  ) -> Union[FluorescenceResult, List[FluorescenceResult]]:
    """Measure the fluorescence intensity of the plate from the top (endpoint), through one
    fluorescence channel or several in one pass.

    The monochromator band edges are the nominal ones, wavelength +/- bandwidth / 2.  Voyager places
    them closer together, by an amount its filter software computes from the wavelength, bandwidth
    and slit; narrow bands differ most (excitation 365/8 and emission 400/10: Voyager 362.9-367.1
    and 397.8-402.2 nm, nominal 361-369 and 395-405 nm), bands of 20 nm and more by a few tenths of
    a nm.  Readings at narrow bandwidths are therefore not directly comparable with Voyager's.  To
    read with Voyager's edges, pass them as ``monochromator``.  Several channels are read in one pass,
    each well through every channel in turn.

    Args:
      fluorescence_channels: one ``FluorescenceChannel``, or a sequence of up to 5 (the reader
        refuses a sixth, quit code 60): excitation and emission wavelength each, the bandwidths (8-100 nm; ``default_excitation_bandwidth`` and
        ``default_emission_bandwidth`` when None) and the dichroic split (None sets it as Voyager
        does automatically, midway between the excitation's upper and the emission's lower edge;
        the tested reader refuses a dichroic above 760 nm, quit code 67).  No filters.
      gain: the detector gain, one for every channel or one per channel; required unless
        ``enhanced_dynamic_range``.
      wells: the wells to measure; None measures every well.
      focal_height: mm above the plate's base, in 0.1 mm steps; None takes
        ``default_focal_height_percent_of_well_depth`` of the wells' depth above their floor
        (``adjust_gain_and_focus`` finds one).
      enhanced_dynamic_range: read with enhanced dynamic range; no gain is sent.
      monochromator: band edges, dichroic and slits to read with instead of the computed ones, one
        per channel.
      plate: the plate to measure; None uses the plate on the plate holder.
      measurement_settings: how to read; None reads with the defaults (20 flashes, 0.5 s).
      shake_settings: shake the plate before the read; None does not shake.
      simulated_data: ignored by a reader; a simulated one (``VANTAstar(simulation=True)``)
        reads its values from it.
      timeout: seconds the run may take.

    Returns:
      The counts of the measured wells: one result for one channel, a list of results (one per
      channel, in the order given) for a sequence.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    _, settings, measured, optics, parameters = self._build_measure(
      fluorescence_channels,
      gain,
      monochromator,
      focal_height,
      enhanced_dynamic_range,
      plate,
      wells,
      measurement_settings,
    )
    await self._prepare(settings, shake_settings)
    start = datetime.now()
    data = await self._driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    results = self._get_results(data, measured, optics, start, end)
    return results[0] if isinstance(fluorescence_channels, FluorescenceChannel) else results

  async def measure_spectrum(
    self,
    scan: ScanSide,
    start_wavelength: int,
    end_wavelength: int,
    *,
    gain: int,
    excitation_wavelength: Optional[float] = None,
    excitation_bandwidth: Optional[float] = None,
    emission_wavelength: Optional[float] = None,
    emission_bandwidth: Optional[float] = None,
    focal_height: Optional[float] = None,
    step_size: int = 1,
    dichroic_split_wavelength: Optional[float] = None,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    timeout: float = 1800.0,
  ) -> FluorescenceSpectrumResult:
    """Measure a top fluorescence spectrum: excitation or emission scanned, the other fixed.

    The band edges are the nominal ones, as in ``measure``; the dichroic follows each
    point unless ``dichroic_split_wavelength`` is given; the slits are those Voyager sets in a
    scan.  The counts are the reader's raw counts.  Voyager shows spectra multiplied by BMG's
    instrument correction curve (a factor per wavelength for the excitation or the emission
    side), so its exported spectra differ from these by that factor.

    Args:
      scan: "excitation" or "emission", scanned from ``start_wavelength`` to ``end_wavelength``.
      start_wavelength: the first wavelength, a whole nm.
      end_wavelength: the last wavelength, above ``start_wavelength``.
      gain: the detector gain (``adjust_gain_and_focus`` at a representative wavelength pair finds
        one).
      excitation_wavelength: nm, the fixed excitation of an emission spectrum; None when
        excitation is scanned.
      excitation_bandwidth: nm, 8-100; ``default_excitation_bandwidth`` when None.
      emission_wavelength: nm, the fixed emission of an excitation spectrum; None when emission is
        scanned.
      emission_bandwidth: nm, 8-100; ``default_emission_bandwidth`` when None.
      focal_height: mm above the plate's base, in 0.1 mm steps; None takes
        ``default_focal_height_percent_of_well_depth`` of the wells' depth above their floor
        (``adjust_gain_and_focus`` finds one).
      step_size: nm between wavelengths, a whole nm dividing the range.
      dichroic_split_wavelength: nm for every point; None to set it per point.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      measurement_settings: how to read; None reads with the defaults (20 flashes per well and
        wavelength, 0.5 s).
      shake_settings: shake the plate before the read; None does not shake.
      timeout: seconds the run may take.

    Returns:
      Each measured well's spectrum, keyed by well identifier.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range, or the fixed wavelength is missing or given for
        the scanned side.
    """
    if scan not in ("excitation", "emission"):
      raise ValueError(f"scan must be 'excitation' or 'emission', got {scan!r}")
    fixed = emission_wavelength if scan == "excitation" else excitation_wavelength
    other = excitation_wavelength if scan == "excitation" else emission_wavelength
    if fixed is None or other is not None:
      raise ValueError(f"a {scan} spectrum takes a fixed wavelength for the other side only")
    if excitation_bandwidth is None:
      excitation_bandwidth = self._default_excitation_bandwidth
    if emission_bandwidth is None:
      emission_bandwidth = self._default_emission_bandwidth
    scanned = self._get_scan_wavelengths(start_wavelength, end_wavelength, step_size)
    for wavelength in (start_wavelength, end_wavelength):
      excitation, emission = (wavelength, fixed) if scan == "excitation" else (fixed, wavelength)
      self._check_optics(excitation, excitation_bandwidth, emission, emission_bandwidth)
    first, last = compute_scan_monochromators(
      scan,
      start_wavelength,
      end_wavelength,
      fixed,
      excitation_bandwidth=excitation_bandwidth,
      emission_bandwidth=emission_bandwidth,
      dichroic=dichroic_split_wavelength,
    )
    plate = self._get_plate(plate)
    settings = self._get_fluorescence_settings(measurement_settings, plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._driver.build_fluorescence_scan_run(
      first,
      last,
      len(scanned),
      gain=gain,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      **self._get_run_fields(settings),
    )
    await self._prepare(settings, shake_settings)
    start = datetime.now()
    data = await self._driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    self._check_wells(data, measured)
    if data.chromatics[0] != len(scanned):
      raise RuntimeError(f"the reader sent {data.chromatics[0]} points, {len(scanned)} expected")
    groups = [data.group(i) for i in range(len(scanned))]  # per point, one count per well
    return FluorescenceSpectrumResult(
      data={
        _get_identifier(r, c): {w: float(groups[i][k]) for i, w in enumerate(scanned)}
        for k, (r, c) in enumerate(measured)
      },
      excitation_wavelength=None if scan == "excitation" else fixed,
      excitation_bandwidth=excitation_bandwidth,
      emission_wavelength=None if scan == "emission" else fixed,
      emission_bandwidth=emission_bandwidth,
      temperature=data.temperature,
      start_timestamp=start,
      end_timestamp=end,
    )

  async def adjust_gain_and_focus(
    self,
    fluorescence_channel: FluorescenceChannel,
    *,
    wells: Sequence[Well],
    focal_height: Optional[float] = None,
    adjust_focus: bool = True,
    adjust_gain: bool = True,
    target_percent: float = 88.0,
    monochromator: Optional[MonochromatorSettings] = None,
    plate: Optional[Plate] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    timeout: float = 300.0,
  ) -> GainFocus:
    """Find the gain and the focal height, or either, for a top fluorescence read on reference
    wells.

    The reader scans the focal height from 16 mm down in 0.2 mm steps and sets the gain so the
    brightest reference well reads at ``target_percent`` of the detector range, as Voyager's
    automatic gain and focus does.  Voyager's autofocus adjusts the focus once more on every well
    of the read, without the gain (``adjust_gain=False``), and reads at the height found.
    ``measure_z_profile`` returns the whole scan of one well.

    Args:
      fluorescence_channel: the read's ``FluorescenceChannel``, as in ``measure``.
      wells: the reference wells.
      focal_height: mm; the gain is adjusted at this height when ``adjust_focus`` is False; None
        takes the default, as in ``measure``.
      adjust_focus: find the focal height.
      adjust_gain: find the gain.
      target_percent: the gain's target, percent of the detector range (1-100).
      monochromator: band edges, dichroic and slits instead of the computed ones.
      plate: the plate; None uses the plate on the plate holder.
      measurement_settings: the read's settings: its ``well_scan`` and ``scan_diameter`` adjust
        over the orbit or spiral the read uses, with its ``flashes``; the others do not apply.
      timeout: seconds the adjustment may take.

    Returns:
      The gain found (0 when not adjusted), the focal height found (None when not adjusted) and
      the well the reader fitted the gain on; pass the gain and the focal height to ``measure``.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if neither is adjusted, or an argument is out of range (``_get_optics``).
    """
    if not adjust_gain and not adjust_focus:
      raise ValueError("adjust the gain, the focus, or both")
    if adjust_gain and not 1 <= target_percent <= 100:
      raise ValueError(f"target_percent must be 1-100, got {target_percent}")
    excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic = self._get_optics(
      fluorescence_channel
    )
    if monochromator is None:
      monochromator = compute_monochromator_settings(
        excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic
      )
    plate = self._get_plate(plate)
    settings = self._get_fluorescence_settings(measurement_settings, plate)
    parameters = self._driver.build_gain_focus(
      monochromator,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      target_percent=target_percent if adjust_gain else 0,
      adjust_focus=adjust_focus,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      well_scan=settings.well_scan,
      scan_diameter=settings.scan_diameter,
      flashes=settings.flashes or _DEFAULT_FLASHES,
    )
    return await self._driver.adjust_gain_and_focus(parameters, timeout=timeout)

  async def measure_z_profile(
    self,
    fluorescence_channel: FluorescenceChannel,
    *,
    well: Well,
    gain_target_percent: Optional[float] = None,
    focal_height_step: float = 0.1,
    monochromator: Optional[MonochromatorSettings] = None,
    plate: Optional[Plate] = None,
    timeout: float = 300.0,
  ) -> VANTAstarZProfileResult:
    """Measure the z-profile of ``well`` from the top and, with a target, fit the gain at its peak,
    to analyse or debug the focus.

    - The reader reads the well from 16 mm (Voyager's start) down in ``focal_height_step`` steps
      to a lowest height it chooses (2.2-2.4 mm on the 96-well plates tested), and computes the
      peak from the smoothed profile (about 12 s in 0.1 or 0.2 mm steps).  The coarser the steps,
      the coarser the peak: in 1.0 mm steps the reader put it at the lowest point, 3.0 mm, where
      0.1 mm steps put it at 3.8 mm.
    - With ``gain_target_percent`` it then fits the gain so the well reads that percent of the
      detector range at the peak (about 12-17 s in all).
    - It is the scan of ``adjust_gain_and_focus`` on one well, which returns only the gain and the
      peak's focal height.

    Args:
      fluorescence_channel: the optics, as in ``measure``.
      well: the well to scan.
      gain_target_percent: 1-100; None measures the z-profile only.
      focal_height_step: mm between the profile's points, 0.1-1.0 in 0.1 mm steps (Voyager's
        automatic focus uses 0.2).
      monochromator: band edges, dichroic and slits instead of the computed ones.
      plate: the plate; None uses the plate on the plate holder.
      timeout: seconds the scan may take.

    Returns:
      The z-profile, its peak, and the gain with a target.

    Raises:
      RuntimeError: if no plate is on the plate holder, or the drawer is open.
      ValueError: if an argument is out of range (``_get_optics``), the well is not on the plate,
        or the reply names a well not on the plate.
      TimeoutError: if the scan takes longer than ``timeout`` seconds.
    """
    if gain_target_percent is not None and not 1 <= gain_target_percent <= 100:
      raise ValueError(f"gain_target_percent must be 1-100, got {gain_target_percent}")
    lowest, highest = _Z_PROFILE_STEP_RANGE
    if not (
      lowest - 1e-9 <= focal_height_step <= highest + 1e-9
      and abs(focal_height_step * 10 - round(focal_height_step * 10)) < 1e-9
    ):
      raise ValueError(
        f"focal_height_step must be {lowest}-{highest} mm in 0.1 mm steps, got {focal_height_step}"
      )
    excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic = self._get_optics(
      fluorescence_channel
    )
    if monochromator is None:
      monochromator = compute_monochromator_settings(
        excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic
      )
    plate = self._get_plate(plate)
    parameters = self._driver.build_gain_focus(
      monochromator,
      focal_height=self._get_focal_height(None, plate, [well]),
      target_percent=0 if gain_target_percent is None else gain_target_percent,
      adjust_focus=True,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, [well]),
      focal_height_step=focal_height_step,
    )
    logger.info(
      "z-profile: %s/%s nm on %s of %s, gain target %s %%",
      excitation,
      emission,
      plate.get_child_identifier(well),
      plate.name,
      gain_target_percent,
    )
    start_timestamp = datetime.now()
    data = await self._driver.measure_z_profile(parameters, timeout=timeout)
    end_timestamp = datetime.now()
    if not (1 <= data.column <= plate.num_items_x and 1 <= data.row <= plate.num_items_y):
      raise ValueError(
        f"the reply names a well not on the plate, got column {data.column}, row {data.row}"
      )
    return VANTAstarZProfileResult(
      well=_get_identifier(data.row - 1, data.column - 1),
      gain=data.gain or None,
      z_profile=data.z_profile,
      average_signal=data.average_signal,
      peak_counts=data.counts or None,
      peak_detector_percent=data.percent or None,
      peak_focal_height=data.peak_focal_height,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )
