"""Top fluorescence intensity: endpoint, kinetic and spectrum reads through the monochromators, and
the automatic gain and focus."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.resources import Plate, Well

from ..responses import GainFocus, MeasurementData
from .measurement import (
  Measurement,
  MonochromatorSettings,
  ScanSide,
  WellScan,
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


class Fluorescence(Measurement):
  """Top fluorescence intensity measurement (RUN, MeasurementMode 0) and the gain and focus
  adjustment (GainPlateCommand).

  Reached as ``driver.fluorescence``.
  """

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    super().__init__(driver)
    self._default_focal_height_percent_of_well_depth = 25.0

  @property
  def default_focal_height_percent_of_well_depth(self) -> float:
    """The focal height a read takes when none is given: this percent of the wells' depth above
    their floor (25 %, as on the CLARIOstar; 6.2 mm on a Thermo SureSTART 96-well plate, where the
    reader's autofocus found 6.2-6.6 mm)."""
    return self._default_focal_height_percent_of_well_depth

  @default_focal_height_percent_of_well_depth.setter
  def default_focal_height_percent_of_well_depth(self, value: float) -> None:
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

  def _build_run(
    self,
    excitation_wavelength: float,
    emission_wavelength: float,
    *,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    focal_height: float,
    gain: Optional[int],
    dichroic_split_wavelength: Optional[float],
    monochromator: Optional[MonochromatorSettings],
    enhanced_dynamic_range: bool,
    plate: Plate,
    wells: Optional[Sequence[Well]],
    flashes: int,
    settling_time: float,
    well_scan: Optional[WellScan],
    cycles: int = 1,
    cycle_time: int = 0,
  ) -> bytes:
    """Check a fluorescence intensity read's arguments and build its RUN fields.

    Raises:
      ValueError: if an argument is out of range.
    """
    self._check_optics(
      excitation_wavelength, excitation_bandwidth, emission_wavelength, emission_bandwidth
    )
    if gain is None and not enhanced_dynamic_range:
      raise ValueError("a gain is required unless enhanced_dynamic_range")
    self._check_flashes(flashes)
    self._check_settling_time(settling_time)
    if monochromator is None:
      monochromator = compute_monochromator_settings(
        excitation_wavelength,
        excitation_bandwidth,
        emission_wavelength,
        emission_bandwidth,
        dichroic_split_wavelength,
      )
    return self._driver.build_fluorescence_run(
      [monochromator],
      gains=[gain or 0],
      focal_height=focal_height,
      flashes=flashes,
      settling_time=settling_time,
      enhanced_dynamic_range=enhanced_dynamic_range,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      well_scan=well_scan,
      cycles=cycles,
      cycle_time=cycle_time,
    )

  def _get_result(
    self,
    data: MeasurementData,
    measured: List[Tuple[int, int]],
    optics: Tuple[float, float, float, float],
    start: datetime,
    end: datetime,
  ) -> FluorescenceResult:
    """The ``FluorescenceResult`` of a fluorescence block; warns of saturated wells.  ``optics``
    is excitation wavelength and bandwidth, emission wavelength and bandwidth."""
    self._check_wells(data, measured)
    counts = data.group(0)
    saturated = sum(1 for count in counts if count >= data.overflow)
    if saturated:
      logger.warning(
        "%d of %d wells read at the reader's ceiling of %d counts",
        saturated,
        len(counts),
        data.overflow,
      )
    return FluorescenceResult(
      data={_get_identifier(r, c): float(v) for (r, c), v in zip(measured, counts)},
      excitation_wavelength=optics[0],
      excitation_bandwidth=optics[1],
      emission_wavelength=optics[2],
      emission_bandwidth=optics[3],
      temperature=data.temperature,
      start_timestamp=start,
      end_timestamp=end,
    )

  async def measure(
    self,
    excitation_wavelength: float,
    emission_wavelength: float,
    *,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    focal_height: Optional[float] = None,
    gain: Optional[int] = None,
    dichroic_split_wavelength: Optional[float] = None,
    monochromator: Optional[MonochromatorSettings] = None,
    enhanced_dynamic_range: bool = False,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    flashes: int = 20,
    settling_time: float = 0.5,
    well_scan: Optional[WellScan] = None,
    timeout: float = 300.0,
  ) -> FluorescenceResult:
    """Measure the fluorescence intensity of the plate from the top (endpoint).

    The monochromator band edges are the nominal ones, wavelength +/- bandwidth / 2.  Voyager places
    them closer together, by an amount its filter software computes from the wavelength, bandwidth
    and slit; narrow bands differ most (excitation 365/8 and emission 400/10: Voyager 362.9-367.1
    and 397.8-402.2 nm, nominal 361-369 and 395-405 nm), bands of 20 nm and more by a few tenths of
    a nm.  Readings at narrow bandwidths are therefore not directly comparable with Voyager's.  To
    read with Voyager's edges, pass them as ``monochromator``.

    Args:
      excitation_wavelength: nm.
      emission_wavelength: nm.
      excitation_bandwidth: nm, 8-100.
      emission_bandwidth: nm, 8-100.
      focal_height: mm above the plate's base, in 0.1 mm steps; None takes
        ``default_focal_height_percent_of_well_depth`` of the wells' depth above their floor
        (``adjust_gain_and_focus`` finds one).
      gain: the detector gain; required unless ``enhanced_dynamic_range``.
      dichroic_split_wavelength: nm; None sets it as Voyager does automatically, midway between the
        excitation's upper and the emission's lower edge.  The tested reader refuses a dichroic
        above 760 nm (``CommandRefusedError``, quit code 67).
      monochromator: band edges, dichroic and slits to read with instead of the computed ones.
      enhanced_dynamic_range: read with enhanced dynamic range; no gain is sent.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      flashes: flashes per well (1-500).
      settling_time: seconds to settle before reading each well (0.02-1.0, 20 ms steps).
      well_scan: average over an orbit or spiral in each well; None reads the centre.
      timeout: seconds the run may take.

    Returns:
      The counts of the measured wells.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    plate = self._get_plate(plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._build_run(
      excitation_wavelength,
      emission_wavelength,
      excitation_bandwidth=excitation_bandwidth,
      emission_bandwidth=emission_bandwidth,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      gain=gain,
      dichroic_split_wavelength=dichroic_split_wavelength,
      monochromator=monochromator,
      enhanced_dynamic_range=enhanced_dynamic_range,
      plate=plate,
      wells=wells,
      flashes=flashes,
      settling_time=settling_time,
      well_scan=well_scan,
    )
    start = datetime.now()
    data = await self._driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    return self._get_result(
      data,
      measured,
      (excitation_wavelength, excitation_bandwidth, emission_wavelength, emission_bandwidth),
      start,
      end,
    )

  async def measure_kinetic(
    self,
    excitation_wavelength: float,
    emission_wavelength: float,
    *,
    cycles: int,
    cycle_time: int,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    focal_height: Optional[float] = None,
    gain: Optional[int] = None,
    dichroic_split_wavelength: Optional[float] = None,
    monochromator: Optional[MonochromatorSettings] = None,
    enhanced_dynamic_range: bool = False,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    flashes: int = 20,
    settling_time: float = 0.5,
    well_scan: Optional[WellScan] = None,
    timeout: Optional[float] = None,
  ) -> List[FluorescenceResult]:
    """Measure the fluorescence intensity of the plate ``cycles`` times, the reader starting a
    cycle every ``cycle_time`` seconds (plate-mode kinetic, as ``measure``).

    Args:
      excitation_wavelength: nm.
      emission_wavelength: nm.
      cycles: reads of the plate (2-1000).
      cycle_time: whole seconds from one cycle's start to the next (1-65535); the reader refuses a
        cycle time shorter than one cycle takes (``CommandRefusedError``, quit code 60).
      excitation_bandwidth: nm, 8-100.
      emission_bandwidth: nm, 8-100.
      focal_height: mm above the plate's base, in 0.1 mm steps; None takes
        ``default_focal_height_percent_of_well_depth`` of the wells' depth above their floor
        (``adjust_gain_and_focus`` finds one).
      gain: the detector gain; required unless ``enhanced_dynamic_range``.
      dichroic_split_wavelength: nm; None as in ``measure``.
      monochromator: band edges, dichroic and slits to read with instead of the computed ones.
      enhanced_dynamic_range: read with enhanced dynamic range; no gain is sent.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      flashes: flashes per well (1-500).
      settling_time: seconds to settle before reading each well (0.02-1.0, 20 ms steps).
      well_scan: average over an orbit or spiral in each well; None reads the centre.
      timeout: seconds the run may take; None allows the cycles plus 300 s.

    Returns:
      One result per cycle; its ``start_timestamp`` is when the reader started the cycle, its
      ``end_timestamp`` when the run's data was in.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    self._check_cycles(cycles, cycle_time)
    plate = self._get_plate(plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self._build_run(
      excitation_wavelength,
      emission_wavelength,
      excitation_bandwidth=excitation_bandwidth,
      emission_bandwidth=emission_bandwidth,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      gain=gain,
      dichroic_split_wavelength=dichroic_split_wavelength,
      monochromator=monochromator,
      enhanced_dynamic_range=enhanced_dynamic_range,
      plate=plate,
      wells=wells,
      flashes=flashes,
      settling_time=settling_time,
      well_scan=well_scan,
      cycles=cycles,
      cycle_time=cycle_time,
    )
    start = datetime.now()
    blocks = await self._driver.run_blocks(
      parameters, timeout=cycles * cycle_time + 300.0 if timeout is None else timeout
    )
    end = datetime.now()
    optics = (excitation_wavelength, excitation_bandwidth, emission_wavelength, emission_bandwidth)
    return [
      self._get_result(block, measured, optics, start + timedelta(seconds=block.start_time), end)
      for block in blocks
    ]

  async def measure_spectrum(
    self,
    scan: ScanSide,
    start_wavelength: int,
    end_wavelength: int,
    *,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    gain: int,
    focal_height: Optional[float] = None,
    excitation_wavelength: Optional[float] = None,
    emission_wavelength: Optional[float] = None,
    step_size: int = 1,
    dichroic_split_wavelength: Optional[float] = None,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    flashes: int = 20,
    settling_time: float = 0.5,
    well_scan: Optional[WellScan] = None,
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
      excitation_bandwidth: nm, 8-100.
      emission_bandwidth: nm, 8-100.
      gain: the detector gain (``adjust_gain_and_focus`` at a representative wavelength pair finds
        one).
      focal_height: mm above the plate's base, in 0.1 mm steps; None takes
        ``default_focal_height_percent_of_well_depth`` of the wells' depth above their floor
        (``adjust_gain_and_focus`` finds one).
      excitation_wavelength: nm, the fixed excitation of an emission spectrum; None when
        excitation is scanned.
      emission_wavelength: nm, the fixed emission of an excitation spectrum; None when emission is
        scanned.
      step_size: nm between wavelengths, a whole nm dividing the range.
      dichroic_split_wavelength: nm for every point; None to set it per point.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      flashes: flashes per well and wavelength (1-500).
      settling_time: seconds to settle before reading each well (0.02-1.0, 20 ms steps).
      well_scan: average over an orbit or spiral in each well; None reads the centre.
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
    scanned = self._get_scan_wavelengths(start_wavelength, end_wavelength, step_size)
    for wavelength in (start_wavelength, end_wavelength):
      excitation, emission = (wavelength, fixed) if scan == "excitation" else (fixed, wavelength)
      self._check_optics(excitation, excitation_bandwidth, emission, emission_bandwidth)
    self._check_flashes(flashes)
    self._check_settling_time(settling_time)
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
    measured = _get_measured_wells(plate, wells)
    parameters = self._driver.build_fluorescence_scan_run(
      first,
      last,
      len(scanned),
      gain=gain,
      focal_height=self._get_focal_height(focal_height, plate, wells),
      flashes=flashes,
      settling_time=settling_time,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      well_scan=well_scan,
    )
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
    excitation_wavelength: float,
    emission_wavelength: float,
    *,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    wells: Sequence[Well],
    focal_height: float = 5.0,
    adjust_focus: bool = True,
    adjust_gain: bool = True,
    target_percent: float = 88.0,
    dichroic_split_wavelength: Optional[float] = None,
    monochromator: Optional[MonochromatorSettings] = None,
    plate: Optional[Plate] = None,
    well_scan: Optional[WellScan] = None,
    flashes: int = 20,
    timeout: float = 300.0,
  ) -> GainFocus:
    """Find the gain and the focal height, or either, for a top fluorescence read on reference
    wells.

    The reader scans the focal height from 16 mm down in 0.2 mm steps and sets the gain so the
    brightest reference well reads at ``target_percent`` of the detector range, as Voyager's
    automatic gain and focus does.  Voyager's autofocus adjusts the focus once more on every well
    of the read, without the gain (``adjust_gain=False``), and reads at the height found.

    Args:
      excitation_wavelength: nm.
      emission_wavelength: nm.
      excitation_bandwidth: nm, 8-100.
      emission_bandwidth: nm, 8-100.
      wells: the reference wells.
      focal_height: mm; the gain is adjusted at this height when ``adjust_focus`` is False.
      adjust_focus: find the focal height.
      adjust_gain: find the gain.
      target_percent: the gain's target, percent of the detector range (1-100).
      dichroic_split_wavelength: nm; None as in ``measure``.
      monochromator: band edges, dichroic and slits instead of the computed ones.
      plate: the plate; None uses the plate on the plate holder.
      well_scan: adjust over the orbit or spiral a well-scanning read uses; None at the centre.
      flashes: flashes per well, sent with ``well_scan`` (1-500).
      timeout: seconds the adjustment may take.

    Returns:
      The gain found (0 when not adjusted) and the focal height found (None when not adjusted);
      pass them to ``measure``.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if neither is adjusted, or an argument is out of range (`_check_optics`).
    """
    if not adjust_gain and not adjust_focus:
      raise ValueError("adjust the gain, the focus, or both")
    if adjust_gain and not 1 <= target_percent <= 100:
      raise ValueError(f"target_percent must be 1-100, got {target_percent}")
    self._check_flashes(flashes)
    self._check_optics(
      excitation_wavelength, excitation_bandwidth, emission_wavelength, emission_bandwidth
    )
    if monochromator is None:
      monochromator = compute_monochromator_settings(
        excitation_wavelength,
        excitation_bandwidth,
        emission_wavelength,
        emission_bandwidth,
        dichroic_split_wavelength,
      )
    plate = self._get_plate(plate)
    parameters = self._driver.build_gain_focus(
      monochromator,
      focal_height=focal_height,
      target_percent=target_percent if adjust_gain else 0,
      adjust_focus=adjust_focus,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
      well_scan=well_scan,
      flashes=flashes,
    )
    return await self._driver.adjust_gain_and_focus(parameters, timeout=timeout)
