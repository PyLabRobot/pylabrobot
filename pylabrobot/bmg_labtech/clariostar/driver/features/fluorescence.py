"""CLARIOstar fluorescence: plate reads, spectra, filter detection and z-profiles.

A plate read goes through one to five fluorescence channels in one pass (`FluorescenceChannel`),
from the top or the bottom optic. Excitation and emission each select their wavelength through a
monochromator or a filter. A spectrum scans the excitation or the emission monochromator.
Filter detection scans every filter position; a z-profile reads one well over its focal heights,
and with a target the reader fits the gain at its peak.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import (
  TYPE_CHECKING,
  Any,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  Union,
  overload,
)

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.lib.optics.holders import (
  DichroicFilterSlide,
  EmissionFilterSlide,
  ExcitationFilterSlide,
  F,
  FilterSlide,
)
from pylabrobot.resources import Coordinate, Plate, Well

from .measurement import (
  Measurement,
  MeasurementConfiguration,
  MeasurementSettings,
  ShakeSettings,
  _get_measured_identifiers,
)

if TYPE_CHECKING:
  from ..configuration import DeviceConfiguration
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

OpticPosition = Literal["top", "bottom"]
Scan = Literal["excitation", "emission"]


# ----------------------------------------
# Results and device facts
# ----------------------------------------


@dataclass
class CLARIOstarFluorescenceResult(FluorescenceResult):
  """A CLARIOstar fluorescence result, with the detector gain of its fluorescence channel.

  Attributes:
    gain: the detector gain, within `FluorescenceConfiguration.gain_range`.
    matrix_points: a matrix scan's counts, every measured point of each well in the order the
      reader sends them, keyed as ``data``; None for any other well scan. ``data`` then holds
      the plain mean of each well's points.
    focal_height: mm above the plate's base; None if not known.
    optic_position: the optic read from, "top" or "bottom"; None if not known.
  """

  gain: int
  matrix_points: Optional[Dict[str, List[float]]] = None
  focal_height: Optional[float] = None
  optic_position: Optional[OpticPosition] = None

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row shares, with the gain, focal height and optic position."""
    return super()._get_base_row(
      read,
      gain=self.gain,
      focal_height=self.focal_height,
      optic_position=self.optic_position,
      **columns,
    )


@dataclass(frozen=True)
class CLARIOstarZProfileResult:
  """The z-scan of one well as the firmware reports it.

  Attributes:
    well: the identifier of the scanned well (``"D6"``).
    gain: the detector gain the firmware fits at the peak for the target; None without a target.
    z_profile: (focal height in mm, counts) from the highest to the lowest point; the counts at a
      gain the firmware chooses per well: for the shape, not comparable across wells.
    average_signal: the firmware's average of the z-profile's five points around
      `peak_focal_height`, counts.
    peak_counts: the counts at `peak_focal_height` and `gain`; None without a target.
    peak_detector_percent: `peak_counts` in percent of the detector's 260,000 counts, as the
      target; None without a target.
    peak_focal_height: the focal height the firmware computes as the z-profile's maximum, in mm.
    temperature: temperature in degrees C; None: no z-scan with the temperature controller on is
      verified, so the reply's temperature, if it holds one, is not decoded.
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
  temperature: Optional[float]
  start_timestamp: datetime
  end_timestamp: datetime


@dataclass(frozen=True)
class CLARIOstarFluorescenceGridResult:
  """One grid: the counts over a square grid of points around `position`.

  Attributes:
    position: the grid's centre, x and y in mm (z 0): on the plate for a well, on the plate holder
      for a given point, both from their left-front corner.
    well: the identifier of the well at `position` (``"A1"``); None for a given point.
    center: where the reader found the signal's centre, in the frame of `position`; None if it
      found none.
    spacing: mm between neighbouring points of the grid.
    counts: the grid's counts, rows of points in the order the reader sends them; which way the
      rows and columns run is not verified.
    focal_height: the focal height the grid was read at, mm.
    gain: the detector gain it was read at.
    start_timestamp: when the scan started.
    end_timestamp: when its last grid and the centres were in.
  """

  position: Coordinate
  well: Optional[str]
  center: Optional[Coordinate]
  spacing: float
  counts: List[List[int]]
  focal_height: float
  gain: int
  start_timestamp: datetime
  end_timestamp: datetime


@dataclass
class CLARIOstarFluorescenceSpectrumResult(FluorescenceSpectrumResult):
  """A CLARIOstar fluorescence spectrum, with the detector gain it was read with.

  Attributes:
    gain: the detector gain, within `FluorescenceConfiguration.gain_range`.
  """

  gain: int

  def _get_base_row(self, read: int, **columns: Any) -> Dict[str, Any]:
    """The columns every row shares, with the gain."""
    return super()._get_base_row(read, gain=self.gain, **columns)


@dataclass
class FluorescenceConfiguration(MeasurementConfiguration):
  """Device facts of the fluorescence measurement."""

  wavelength_range: Tuple[int, int] = (320, 840)
  """Excitation and emission wavelengths, in nm, with an extended-red photomultiplier; up to 740
  without (`Fluorescence._get_reader_configuration`)."""
  bandwidth_range: Tuple[int, int] = (8, 100)
  """Excitation and emission bandwidths, in nm."""
  dichroic_range: Tuple[float, float] = (340.0, 760.0)
  """Where the dichroic mirror splits excitation from emission, in nm; up to 740 without an
  extended-red photomultiplier."""
  gain_range: Tuple[int, int] = (0, 4095)
  """Detector gain (0 to 4095)."""
  focal_height_range: Tuple[float, float] = (0.0, 25.0)
  """Focal height, in mm, in 0.1 mm steps (up to 25 mm)."""
  excitation_filter_slots: int = 4
  """Excitation filter positions (up to 4); the reader's (`Fluorescence._compute_filter_slots`)."""
  dichroic_filter_slots: int = 3
  """Dichroic filter positions (up to 3); the reader's."""
  emission_filter_slots: int = 4
  """Emission filter positions (up to 4); the reader's."""
  filter_slots_per_slide: int = 2
  """Filter positions on each of the two excitation or emission slides; the reader's."""
  maximum_fluorescence_channels: int = 5
  """Fluorescence channels per read (up to 5)."""
  maximum_spectrum_wavelengths: int = 1000
  """Wavelengths per spectrum (up to 1000)."""


# ----------------------------------------
# Wire values of the optics
# ----------------------------------------


def _compute_band_edges(wavelength: float, bandwidth: float) -> Tuple[int, int]:
  """The band's upper and lower edge in 0.1 nm: `wavelength` +/- half `bandwidth`.

  The nominal edges.
  """
  return round((wavelength + bandwidth / 2) * 10), round((wavelength - bandwidth / 2) * 10)


def _compute_dichroic(excitation: Tuple[int, int], emission: Tuple[int, int]) -> int:
  """The default dichroic split in 0.1 nm, rounded half to even.

  Midway between the two bands' facing edges: the excitation band's top and the emission band's
  bottom, or the other way round with excitation above emission. Each band is (upper, lower) edge
  in 0.1 nm (`_compute_band_edges`).
  """
  if excitation[0] < emission[1]:
    return round((excitation[0] + emission[1]) / 2)
  return round((emission[0] + excitation[1]) / 2)


def _compute_filter_positions(slot: int, per_slide: int) -> Tuple[int, int]:
  """The motor positions of the two excitation or emission slides for filter `slot`.

  The first slide's first; 1 is a slide's home, 2 its first filter (the reader's filter
  detection).
  """
  if slot <= per_slide:
    return slot + 1, 1
  return 1, slot - per_slide + 1


class Fluorescence(Measurement):
  """Fluorescence measurement.

  Reached as `driver.fluorescence`, built at setup only if the reader reports a fluorescence
  measurement channel.
  """

  configuration: FluorescenceConfiguration

  _MODE = 0x00
  _WELL_SCAN_CODE = 0x03
  _TRAILER_START = 0x00

  # Without an extended-red photomultiplier reading fluorescence, excitation, emission and a
  # dichroic split above 740 nm are refused.
  _STANDARD_WAVELENGTH_RANGE = (320, 740)
  _STANDARD_DICHROIC_RANGE = (340.0, 740.0)

  # The reader's slides holding filters: two for excitation, two for emission, one for dichroics.
  _EXCITATION_SLIDES = ("EX_SP", "EX_LP")
  _EMISSION_SLIDES = ("EM_SP", "EM_LP")
  _DICHROIC_SLIDE = "BSP"

  @classmethod
  def _compute_filter_slots(
    cls, slides: Optional[Dict[str, Dict[int, str]]]
  ) -> Optional[Tuple[int, int]]:
    """The filters per excitation or emission slide, and the dichroic filters, in `slides`.

    A slide's positions other than "open" hold filters. None keeps the defaults: a slide not
    reported, or excitation and emission slides of different sizes (one size serves them all).
    """
    names = (*cls._EXCITATION_SLIDES, *cls._EMISSION_SLIDES, cls._DICHROIC_SLIDE)
    if slides is None or any(name not in slides for name in names):
      return None
    filters = {
      name: sum(label.strip().lower() != "open" for label in slides[name].values())
      for name in names
    }
    per_slide = {filters[name] for name in (*cls._EXCITATION_SLIDES, *cls._EMISSION_SLIDES)}
    if len(per_slide) != 1 or not filters[cls._DICHROIC_SLIDE]:
      logger.warning("filter slides of %s are not decoded; the default filter slots stay", filters)
      return None
    (size,) = per_slide
    return (size, filters[cls._DICHROIC_SLIDE]) if size else None

  @classmethod
  def _get_reader_configuration(cls, device: "DeviceConfiguration") -> FluorescenceConfiguration:
    """The fluorescence facts of the reader `device` describes: wavelength limits, filter slots.

    The extended range needs the extended-red photomultiplier on the fluorescence path: fitted,
    and on a dual-photomultiplier reader not crossed over. The filter slots are its slides'
    (`_compute_filter_slots`). Unknown hardware keeps the defaults.
    """
    configuration = FluorescenceConfiguration()
    slots = cls._compute_filter_slots(device.monochromator_slides)
    if slots is not None:
      per_slide, dichroic = slots
      configuration = replace(
        configuration,
        excitation_filter_slots=2 * per_slide,
        dichroic_filter_slots=dichroic,
        emission_filter_slots=2 * per_slide,
        filter_slots_per_slide=per_slide,
      )
    standard = device.extended_red_photomultiplier_tube is False or bool(
      device.dual_photomultiplier_tubes and device.photomultiplier_tube_crossover
    )
    if standard:
      return replace(
        configuration,
        wavelength_range=cls._STANDARD_WAVELENGTH_RANGE,
        dichroic_range=cls._STANDARD_DICHROIC_RANGE,
      )
    return configuration

  def __init__(
    self, driver: "CLARIOstarDriver", configuration: Optional[FluorescenceConfiguration] = None
  ):
    """Build the fluorescence feature of `driver`.

    Args:
      driver: the driver to send commands through.
      configuration: the fluorescence's device facts; None for the reader's
        (`_get_reader_configuration`).
    """
    super().__init__(driver, configuration or self._get_reader_configuration(driver.configuration))
    self._default_excitation_bandwidth = 15  # nm
    self._default_emission_bandwidth = 20  # nm
    self._default_optic_position: OpticPosition = "top"
    # The filters each slide holds, by slot: `detect_all_filters`, or assigned; None until then.
    self.excitation_filter_slide: Optional[ExcitationFilterSlide] = None
    self.dichroic_filter_slide: Optional[DichroicFilterSlide] = None
    self.emission_filter_slide: Optional[EmissionFilterSlide] = None
    self._default_focal_height_percent_of_well_depth = 25.0

  # ----------------------------------------
  # Defaults and filters from the slides
  # ----------------------------------------

  def _check_default_bandwidth(self, name: str, value: int) -> None:
    """Raise unless `value` is a bandwidth `_check_bandwidth` accepts."""
    errors = self._check_bandwidth(name, value, self.configuration.bandwidth_range)
    if errors:
      raise ValueError(errors[0])

  @property
  def default_excitation_bandwidth(self) -> int:
    """Excitation bandwidth, nm, of a monochromator read given none (15 by default)."""
    return self._default_excitation_bandwidth

  @default_excitation_bandwidth.setter
  def default_excitation_bandwidth(self, value: int) -> None:
    """Set the default, raising if `_check_bandwidth` refuses it."""
    self._check_default_bandwidth("default_excitation_bandwidth", value)
    self._default_excitation_bandwidth = value

  @property
  def default_emission_bandwidth(self) -> int:
    """Emission bandwidth, nm, of a monochromator read given none (20 by default)."""
    return self._default_emission_bandwidth

  @default_emission_bandwidth.setter
  def default_emission_bandwidth(self, value: int) -> None:
    """Set the default, raising if `_check_bandwidth` refuses it."""
    self._check_default_bandwidth("default_emission_bandwidth", value)
    self._default_emission_bandwidth = value

  # Pre-separator byte 0: the optic the read uses (bottom `40`).
  _OPTIC_POSITIONS: Dict[str, int] = {"top": 0x00, "bottom": 0x40}

  def _check_optic_position(self, optic_position: str, name: str = "optic_position") -> List[str]:
    """The violation of an optic position `name`: unknown, or the bottom on a reader without one."""
    if optic_position not in self._OPTIC_POSITIONS:
      return [f"{name} must be one of {list(self._OPTIC_POSITIONS)}, got {optic_position!r}"]
    if (
      optic_position == "bottom"
      and self._driver.configuration.bottom_measurement_available is False
    ):
      return [f"the reader has no bottom optic, got {name} 'bottom'"]
    return []

  @property
  def default_optic_position(self) -> OpticPosition:
    """The optic a read uses given none (top by default)."""
    return self._default_optic_position

  @default_optic_position.setter
  def default_optic_position(self, value: OpticPosition) -> None:
    """Set the default, raising if `_check_optic_position` refuses it."""
    errors = self._check_optic_position(value, "default_optic_position")
    if errors:
      raise ValueError(errors[0])
    self._default_optic_position = value

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

  @staticmethod
  def _get_resolved_filter(value: Union[F, int, str, None]) -> Optional[F]:
    """A fluorescence channel's filter, once `_get_filled_fluorescence_channel` has resolved it.

    Raises:
      TypeError: if `value` is still a slot or a name.
    """
    if isinstance(value, (int, str)):
      raise TypeError(f"a filter must be resolved through its filter slide, got {value!r}")
    return value

  @staticmethod
  def _get_filter(
    filter_slide: Optional[FilterSlide[F]], value: Union[F, int, str], slide: str
  ) -> F:
    """The filter `value` names: itself, the slide's filter at a slot, or its filter of a name.

    An empty slot within the slide is an anonymous filter (`FilterSlide.__getitem__`).

    Args:
      slide: "excitation", "dichroic" or "emission", the prefix of the field named in the errors.

    Raises:
      ValueError: if a slot or a name is given without a slide, a slot is outside the slide, or
        no filter of the slide has the name.
    """
    if not isinstance(value, (int, str)):
      return value
    if filter_slide is None:
      raise ValueError(
        f"{slide}_filter as a slot or a name needs a {slide} filter slide (run detect_all_filters "
        f"or assign {slide}_filter_slide), got {value!r}"
      )
    if isinstance(value, str):
      try:
        return filter_slide.by_name(value)
      except KeyError as error:
        raise ValueError(error.args[0]) from None
    if not 1 <= value <= filter_slide.maximum_slots:
      raise ValueError(
        f"{slide}_filter slot must be 1 to {filter_slide.maximum_slots}, got {value}"
      )
    return filter_slide[value]

  def _get_filled_fluorescence_channel(
    self, fluorescence_channel: FluorescenceChannel
  ) -> FluorescenceChannel:
    """A copy of `fluorescence_channel` with its filters from the slides and its bandwidths filled.

    - A filter given as a slot or a name is its slide's (`_get_filter`).
    - Excitation or emission without a filter or a bandwidth has the default bandwidth.

    Raises:
      ValueError: if a slot or a name has no slide, a slot is outside its slide, or no filter of
        the slide has the name.
    """
    excitation_filter = fluorescence_channel.excitation_filter
    if excitation_filter is not None:
      excitation_filter = self._get_filter(
        self.excitation_filter_slide, excitation_filter, "excitation"
      )
    dichroic_filter = fluorescence_channel.dichroic_filter
    if dichroic_filter is not None:
      dichroic_filter = self._get_filter(self.dichroic_filter_slide, dichroic_filter, "dichroic")
    emission_filter = fluorescence_channel.emission_filter
    if emission_filter is not None:
      emission_filter = self._get_filter(self.emission_filter_slide, emission_filter, "emission")
    excitation_bandwidth = fluorescence_channel.excitation_bandwidth
    if excitation_bandwidth is None and excitation_filter is None:
      excitation_bandwidth = self._default_excitation_bandwidth
    emission_bandwidth = fluorescence_channel.emission_bandwidth
    if emission_bandwidth is None and emission_filter is None:
      emission_bandwidth = self._default_emission_bandwidth
    return replace(
      fluorescence_channel,
      excitation_filter=excitation_filter,
      dichroic_filter=dichroic_filter,
      emission_filter=emission_filter,
      excitation_bandwidth=excitation_bandwidth,
      emission_bandwidth=emission_bandwidth,
    )

  # ----------------------------------------
  # Fluorescence channels
  # ----------------------------------------

  # -- checks --------------------------------------------------------------------------------------

  def _check_excitation_or_emission_on_reader(
    self,
    excitation_or_emission: Scan,
    wavelength: Optional[float],
    bandwidth: Optional[float],
    optical_filter: Optional[OpticalFilter],
    *,
    scanned: bool = False,
  ) -> List[str]:
    """The violations of excitation or emission on the reader.

    Neither a wavelength nor a filter (unless `scanned`), a wavelength not a whole nm within
    `wavelength_range` or on a reader without a monochromator (`_check_monochromator`), a
    bandwidth `_check_bandwidth` refuses, a filter slot outside its filter slots.

    Args:
      excitation_or_emission: "excitation" or "emission", the prefix of the fields named in the
        errors.
    """
    if optical_filter is not None:
      slots = {
        "excitation": self.configuration.excitation_filter_slots,
        "emission": self.configuration.emission_filter_slots,
      }[excitation_or_emission]
      if not isinstance(optical_filter.slot, int) or not 1 <= optical_filter.slot <= slots:
        return [
          f"{excitation_or_emission}_filter slot must be 1 to {slots}, got {optical_filter.slot!r}"
        ]
      return []
    errors = []
    if wavelength is not None or scanned:
      errors += self._check_monochromator(
        f"{excitation_or_emission}_wavelength; give {excitation_or_emission}_filter"
      )
    if wavelength is None:
      if not scanned:
        errors.append(
          f"give {excitation_or_emission}_wavelength or {excitation_or_emission}_filter"
        )
    elif not isinstance(wavelength, int):
      errors.append(f"{excitation_or_emission}_wavelength must be a whole nm, got {wavelength!r}")
    else:
      lowest, highest = self.configuration.wavelength_range
      if not lowest <= wavelength <= highest:
        errors.append(
          f"{excitation_or_emission}_wavelength must be {lowest} to {highest} nm, got {wavelength}"
        )
    if bandwidth is not None:
      errors += self._check_bandwidth(
        f"{excitation_or_emission}_bandwidth", bandwidth, self.configuration.bandwidth_range
      )
    return errors

  def _check_bands(
    self,
    excitation_wavelength: float,
    excitation_bandwidth: float,
    emission_wavelength: float,
    emission_bandwidth: float,
    *,
    split: Optional[float],
    dichroic_filter: Optional[DichroicFilter],
  ) -> List[str]:
    """The violations of a fluorescence channel's excitation and emission bands together.

    Overlapping bands; without a dichroic filter, a split not between the wavelengths, or any split
    outside `dichroic_range`. Excitation above emission is accepted with the untested-on-hardware
    warning.
    """
    excitation = _compute_band_edges(excitation_wavelength, excitation_bandwidth)
    emission = _compute_band_edges(emission_wavelength, emission_bandwidth)
    # Bands are (upper, lower) edges; excitation may lie below or above emission.
    if not (excitation[0] < emission[1] or emission[0] < excitation[1]):
      return [
        f"the excitation and emission bands must not overlap, got excitation "
        f"{excitation[1] / 10}-{excitation[0] / 10} nm and emission "
        f"{emission[1] / 10}-{emission[0] / 10} nm"
      ]
    if dichroic_filter is not None:
      return []
    errors = []
    lower, upper = sorted((excitation_wavelength, emission_wavelength))
    if split is None:
      split = _compute_dichroic(excitation, emission) / 10
    elif not lower < split < upper:
      errors.append(
        f"dichroic_split_wavelength must lie between {lower} and {upper} nm, got {split}"
      )
    if emission[0] < excitation[1]:
      # TODO(device): no read with excitation above emission is verified.
      self._warn_untested("a fluorescence read with excitation above emission")
    lowest_split, highest_split = self.configuration.dichroic_range
    if not lowest_split <= split <= highest_split:
      errors.append(f"the dichroic split must be {lowest_split} to {highest_split} nm, got {split}")
    return errors

  def _check_fluorescence_channel(
    self, fluorescence_channel: FluorescenceChannel, *, scanned: Optional[Scan] = None
  ) -> List[str]:
    """The violations of a fluorescence channel on the reader: excitation, emission, dichroic.

    The bands are compared only when nothing else is wrong and excitation and emission both have
    a wavelength and a bandwidth.

    Args:
      fluorescence_channel: the fluorescence channel, its filters resolved and its bandwidths
        filled.
      scanned: a spectrum's scanned excitation or emission, which has no wavelength.

    Returns:
      every rule broken: excitation or emission as `_check_excitation_or_emission_on_reader`
      refuses, an excitation or emission filter without a `dichroic_filter`, a split not in
      0.1 nm steps, a dichroic slot outside `dichroic_filter_slots`, `_check_bands`'s.
    """
    dichroic_filter = self._get_resolved_filter(fluorescence_channel.dichroic_filter)
    errors = self._check_excitation_or_emission_on_reader(
      "excitation",
      fluorescence_channel.excitation_wavelength,
      fluorescence_channel.excitation_bandwidth,
      self._get_resolved_filter(fluorescence_channel.excitation_filter),
      scanned=scanned == "excitation",
    ) + self._check_excitation_or_emission_on_reader(
      "emission",
      fluorescence_channel.emission_wavelength,
      fluorescence_channel.emission_bandwidth,
      self._get_resolved_filter(fluorescence_channel.emission_filter),
      scanned=scanned == "emission",
    )
    if dichroic_filter is None and (
      fluorescence_channel.excitation_filter is not None
      or fluorescence_channel.emission_filter is not None
    ):
      # No read with an excitation or emission filter and the dichroic mirror is verified.
      errors.append("an excitation or emission filter needs a dichroic_filter")
    split = fluorescence_channel.dichroic_split_wavelength
    if split is not None and (
      not isinstance(split, (int, float)) or abs(split * 10 - round(split * 10)) > 1e-9
    ):
      errors.append(f"dichroic_split_wavelength must be nm in 0.1 nm steps, got {split!r}")
    if dichroic_filter is not None:
      slots = self.configuration.dichroic_filter_slots
      slot = dichroic_filter.slot
      if not isinstance(slot, int) or not 1 <= slot <= slots:
        errors.append(f"dichroic_filter slot must be 1 to {slots}, got {slot!r}")
    if (
      not errors
      and fluorescence_channel.excitation_wavelength is not None
      and fluorescence_channel.excitation_bandwidth is not None
      and fluorescence_channel.emission_wavelength is not None
      and fluorescence_channel.emission_bandwidth is not None
    ):
      errors += self._check_bands(
        fluorescence_channel.excitation_wavelength,
        fluorescence_channel.excitation_bandwidth,
        fluorescence_channel.emission_wavelength,
        fluorescence_channel.emission_bandwidth,
        split=split,
        dichroic_filter=dichroic_filter,
      )
    return errors

  def _check_read(
    self,
    focal_height: Optional[float],
    measurement: MeasurementSettings,
    timeout: Optional[float],
  ) -> List[str]:
    """The violations every fluorescence read shares.

    `_check_focal_height`'s (None is the default's, checked with the plate), a pause before
    reading, a timeout not positive.
    """
    errors = (
      []
      if focal_height is None
      else self._check_focal_height(focal_height, self.configuration.focal_height_range)
    )
    if measurement.pause_before_reading is not None:
      errors.append("a pause before reading is not supported for fluorescence")
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    return errors

  # -- RUN parameters ------------------------------------------------------------------------------

  # After the focal height: the kind of read, discrete 00 or spectrum 02.
  _DISCRETE = 0x00
  _SPECTRUM = 0x02
  # Between two fluorescence channels' blocks.
  _FLUORESCENCE_CHANNEL_SEPARATOR = bytes.fromhex("00000c")
  # The excitation and emission wavelength selectors, after the emission edges: a plate read's
  # monochromator or filter; a spectrum's scanned and fixed monochromators.
  _WAVELENGTH_SELECTORS: Dict[str, int] = {
    "excitation_monochromator": 0x03,
    "emission_monochromator": 0x04,
    "filter": 0x01,
    "scanned_monochromator": 0x02,
    "fixed_monochromator": 0x03,
  }
  # Pause before reading: off. No fluorescence read with a pause is verified.
  _NO_PAUSE = bytes(3)

  def _compute_optics(
    self, fluorescence_channel: FluorescenceChannel
  ) -> Tuple[Tuple[int, int], int, Tuple[int, int]]:
    """A fluorescence channel's excitation fields, dichroic and emission fields.

    Through a monochromator, excitation's or emission's fields are its band edges in 0.1 nm;
    through a filter, the motor positions of its two slides (`_compute_filter_positions`),
    emission's in reverse order. The dichroic is a fixed one's slot + 1, a split in 0.1 nm, or
    `_compute_dichroic`.

    Raises:
      ValueError: if excitation or emission has neither a filter nor a wavelength and bandwidth.
    """
    per_slide = self.configuration.filter_slots_per_slide
    excitation_filter = self._get_resolved_filter(fluorescence_channel.excitation_filter)
    emission_filter = self._get_resolved_filter(fluorescence_channel.emission_filter)
    dichroic_filter = self._get_resolved_filter(fluorescence_channel.dichroic_filter)
    excitation_wavelength = fluorescence_channel.excitation_wavelength
    excitation_bandwidth = fluorescence_channel.excitation_bandwidth
    emission_wavelength = fluorescence_channel.emission_wavelength
    emission_bandwidth = fluorescence_channel.emission_bandwidth
    if excitation_filter is not None:
      excitation = _compute_filter_positions(excitation_filter.slot, per_slide)
    elif excitation_wavelength is not None and excitation_bandwidth is not None:
      excitation = _compute_band_edges(excitation_wavelength, excitation_bandwidth)
    else:
      raise ValueError("give excitation_wavelength and excitation_bandwidth, or excitation_filter")
    if emission_filter is not None:
      first, second = _compute_filter_positions(emission_filter.slot, per_slide)
      emission = (second, first)
    elif emission_wavelength is not None and emission_bandwidth is not None:
      emission = _compute_band_edges(emission_wavelength, emission_bandwidth)
    else:
      raise ValueError("give emission_wavelength and emission_bandwidth, or emission_filter")
    if dichroic_filter is not None:
      dichroic = dichroic_filter.slot + 1
    elif fluorescence_channel.dichroic_split_wavelength is not None:
      dichroic = round(fluorescence_channel.dichroic_split_wavelength * 10)
    else:
      dichroic = _compute_dichroic(excitation, emission)
    return excitation, dichroic, emission

  def _compute_wavelength_selectors(
    self, fluorescence_channel: FluorescenceChannel
  ) -> Tuple[int, int]:
    """The wavelength selectors, emission then excitation: each one's monochromator, or a filter."""
    selectors = self._WAVELENGTH_SELECTORS
    return (
      selectors[
        "emission_monochromator" if fluorescence_channel.emission_filter is None else "filter"
      ],
      selectors[
        "excitation_monochromator" if fluorescence_channel.excitation_filter is None else "filter"
      ],
    )

  @staticmethod
  def _encode_optics(
    excitation: Tuple[int, int],
    dichroic: int,
    emission: Tuple[int, int],
    emission_selector: int,
    excitation_selector: int,
  ) -> bytes:
    """A fluorescence channel's 14 optics bytes, u16 BE fields then the wavelength selectors.

    The excitation fields, the dichroic, the emission fields, then `00 EM 00 EX`.
    """
    fields = b"".join(value.to_bytes(2, "big") for value in (*excitation, dichroic, *emission))
    return fields + bytes([0, emission_selector, 0, excitation_selector])

  def _encode_fluorescence_channel(
    self,
    fluorescence_channel: FluorescenceChannel,
    gain: int,
    selectors: Optional[Tuple[int, int]] = None,
  ) -> bytes:
    """One fluorescence channel's 17 bytes: gain u16 BE, the optics, `00`.

    Args:
      selectors: the wavelength selectors, emission then excitation; None for each one's
        monochromator or filter (`_compute_wavelength_selectors`).
    """
    if selectors is None:
      selectors = self._compute_wavelength_selectors(fluorescence_channel)
    optics = self._encode_optics(*self._compute_optics(fluorescence_channel), *selectors)
    return gain.to_bytes(2, "big") + optics + b"\x00"

  def _compute_run_parameters(
    self,
    plate: Plate,
    wells: List[Well],
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    *,
    focal_height: float,
    kind: int,
    count: int,
    encoded_fluorescence_channels: Sequence[bytes],
    optics: int = 0,
    flying_mode: bool = False,
    enhanced_dynamic_range: bool = False,
    scan_byte: Optional[int] = None,
  ) -> bytes:
    """The RUN parameters of a fluorescence read or spectrum.

    The run start (`_compute_run_start`, with `optics`, `flying_mode`, `enhanced_dynamic_range`
    and `scan_byte`), the focal height in 0.01 mm, `kind`, `count` u16 BE and `00 00 00 00 00 0c`,
    the `encoded_fluorescence_channels` (`_encode_fluorescence_channel`), no pause, the run end.

    Args:
      kind: `_DISCRETE`, or `_SPECTRUM` with the start and end fluorescence channels between
        which the reader steps.
      count: the fluorescence channels of a discrete read, or a spectrum's wavelengths.
    """
    return (
      self._compute_run_start(
        plate,
        wells,
        measurement,
        shake,
        optics=optics,
        flying_mode=flying_mode,
        enhanced_dynamic_range=enhanced_dynamic_range,
        scan_byte=scan_byte,
      )
      + round(focal_height * 100).to_bytes(2, "big")
      + bytes([kind])
      + count.to_bytes(2, "big")
      + bytes(5)
      + b"\x0c"
      + self._FLUORESCENCE_CHANNEL_SEPARATOR.join(encoded_fluorescence_channels)
      + self._NO_PAUSE
      + self._compute_run_end(measurement.flashes)
    )

  # -- the log and the results ---------------------------------------------------------------------

  def _describe_fluorescence_channel(
    self, fluorescence_channel: FluorescenceChannel, gain: Optional[int] = None
  ) -> str:
    """One fluorescence channel of the read, for the log: excitation, emission and the gain.

    Excitation and emission each as wavelength and bandwidth, or filter; the gain only when given.
    """

    def describe(
      wavelength: Optional[float],
      bandwidth: Optional[float],
      optical_filter: Optional[OpticalFilter],
    ) -> str:
      if optical_filter is not None:
        return f"filter {optical_filter.slot}" + (
          f" ({optical_filter.name})" if optical_filter.name else ""
        )
      return f"{wavelength}/{bandwidth} nm"

    excitation = describe(
      fluorescence_channel.excitation_wavelength,
      fluorescence_channel.excitation_bandwidth,
      self._get_resolved_filter(fluorescence_channel.excitation_filter),
    )
    emission = describe(
      fluorescence_channel.emission_wavelength,
      fluorescence_channel.emission_bandwidth,
      self._get_resolved_filter(fluorescence_channel.emission_filter),
    )
    return f"{excitation} -> {emission}" + ("" if gain is None else f", gain {gain}")

  def _get_result_optics(self, fluorescence_channel: FluorescenceChannel) -> Dict[str, Any]:
    """A fluorescence channel's optics as a result's keyword arguments.

    Excitation's and emission's filter passband, or monochromator wavelength and the bandwidth the
    read used.
    """
    excitation_filter = self._get_resolved_filter(fluorescence_channel.excitation_filter)
    emission_filter = self._get_resolved_filter(fluorescence_channel.emission_filter)
    optics: Dict[str, Any] = {}
    if excitation_filter is not None:
      optics["excitation_wavelength"] = excitation_filter.center_wavelength
      optics["excitation_bandwidth"] = excitation_filter.bandwidth
    else:
      optics["excitation_wavelength"] = fluorescence_channel.excitation_wavelength
      optics["excitation_bandwidth"] = fluorescence_channel.excitation_bandwidth
    if emission_filter is not None:
      optics["emission_wavelength"] = emission_filter.center_wavelength
      optics["emission_bandwidth"] = emission_filter.bandwidth
    else:
      optics["emission_wavelength"] = fluorescence_channel.emission_wavelength
      optics["emission_bandwidth"] = fluorescence_channel.emission_bandwidth
    return optics

  # ----------------------------------------
  # Filter detection
  # ----------------------------------------

  # Filter detection `24`, mode byte: the slide's code OR bit 5, always set.
  _FILTER_SCAN_SLIDES: Dict[str, int] = {"excitation": 0x00, "emission": 0x01, "dichroic": 0x03}
  _FILTER_SCAN_BIT_5 = 0x20
  # The emission detector gains an emission scan sends before the slide positions; excitation and
  # dichroic scans send zeros.
  _EMISSION_SCAN_GAINS = (1100, 1200, 1100, 1150)
  # Seconds one position may take: excitation and dichroic 6.4 s at most, emission 48.6 s.
  _FILTER_SCAN_TIMEOUT = 20.0
  _EMISSION_FILTER_SCAN_TIMEOUT = 100.0
  # FILTER_RESULT byte 6: the slide's code OR 0x80, which brings the slopes and the curve's range.
  _FILTER_RESULT_LAYOUT = 0x80

  # -- `24` parameters -----------------------------------------------------------------------------

  def _compute_filter_scan_parameters(self, slide: str, slot: int) -> bytes:
    """`24`'s parameters for one filter position: the mode, then 13 words u16 BE.

    - The first four: `_EMISSION_SCAN_GAINS` for emission, zeros otherwise.
    - Then the slide positions: two slits, EX_SP, EX_LP, BSP, EM_SP, EM_LP, EM_SLIT, EX_SLIT
      (the reader's slides), 1 their home.
    - A filter's positions are the RUN's (`_compute_filter_positions`): excitation (EX_SP, EX_LP),
      dichroic BSP = slot + 1, emission (EM_LP, EM_SP).
    """
    per_slide = self.configuration.filter_slots_per_slide
    excitation, dichroic, emission = (1, 1), 1, (1, 1)
    if slide == "excitation":
      excitation = _compute_filter_positions(slot, per_slide)
    elif slide == "dichroic":
      dichroic = slot + 1
    else:
      long_pass, short_pass = _compute_filter_positions(slot, per_slide)
      emission = (short_pass, long_pass)
    gains = self._EMISSION_SCAN_GAINS if slide == "emission" else (0, 0, 0, 0)
    words = (*gains, 1, 1, *excitation, dichroic, *emission, 1, 1)
    mode = self._FILTER_SCAN_SLIDES[slide] | self._FILTER_SCAN_BIT_5
    return bytes([mode]) + b"".join(word.to_bytes(2, "big") for word in words)

  # -- decoding ------------------------------------------------------------------------------------

  def _decode_filter_result(self, reply: bytes, slide: str) -> Tuple[int, int, int, int, int]:
    """A FILTER_RESULT (`05 1b`) reply's bandwidth, two edges and two slopes.

    - The edges are the band's thresholds; bandwidth and edges in 0.1 nm.
    - Bytes 7-14: peak, bandwidth, edge 1, edge 2 (u16 BE).
    - Bytes 15-18: the slopes (s16 BE); 19-22: the curve's range.
    - Bytes 23-24: the point count n, then n points of 8 bytes and 8 bytes `00`.

    Raises:
      ValueError: if the reply is not a whole FILTER_RESULT of `slide`.
    """
    layout = self._FILTER_SCAN_SLIDES[slide] | self._FILTER_RESULT_LAYOUT
    if len(reply) < 25 or reply[6] != layout:
      raise ValueError(f"not a filter result of the {slide} slide, got {reply[:7].hex()}")
    points = int.from_bytes(reply[23:25], "big")
    if len(reply) < 25 + 8 * points + 8:
      raise ValueError(f"filter result cut short: {points} points, got {len(reply)} bytes")
    bandwidth, first_edge, second_edge = (
      int.from_bytes(reply[offset : offset + 2], "big") for offset in (9, 11, 13)
    )
    first_slope, second_slope = (
      int.from_bytes(reply[offset : offset + 2], "big", signed=True) for offset in (15, 17)
    )
    return bandwidth, first_edge, second_edge, first_slope, second_slope

  @staticmethod
  def _compute_optical_filter(
    slot: int, result: Tuple[int, int, int, int, int]
  ) -> Optional[OpticalFilter]:
    """The excitation or emission filter a FILTER_RESULT describes; None for an empty position.

    By the result's fields:
    - a bandwidth: a band-pass between the edges;
    - else a first edge with a slope: a long-pass, open above;
    - else a second edge with a slope: a short-pass, its band not stated (not verified).
    """
    bandwidth, first_edge, second_edge, first_slope, second_slope = result
    if bandwidth > 0:
      return OpticalFilter(slot, passbands=((first_edge / 10, second_edge / 10),))
    if first_edge > 0 and first_slope != 0:
      return OpticalFilter(slot, passbands=((first_edge / 10, None),))
    if second_edge > 0 and second_slope != 0:
      # TODO(device): no short-pass filter is verified; state its band once a reader reports one.
      logger.warning(
        "filter %d is a short-pass up to %s nm; its band is not stated", slot, second_edge / 10
      )
      return OpticalFilter(slot)
    return None

  @staticmethod
  def _compute_dichroic_filter(
    slot: int, result: Tuple[int, int, int, int, int]
  ) -> Optional[DichroicFilter]:
    """The dichroic a FILTER_RESULT describes; None for an empty position.

    By the result's fields:
    - a first edge: a long-pass that transmits from it, open above;
    - else a second edge: a short-pass, its bands not stated (not verified).
    """
    _, first_edge, second_edge, _, _ = result
    if first_edge > 0:
      return DichroicFilter(slot, transmission_bands=((first_edge / 10, None),))
    if second_edge > 0:
      # TODO(device): no short-pass dichroic is verified; state its bands once a reader reports one.
      logger.warning(
        "dichroic %d is a short-pass at %s nm; its bands are not stated", slot, second_edge / 10
      )
      return DichroicFilter(slot)
    return None

  # -- detecting -----------------------------------------------------------------------------------

  async def _detect_filter(self, slide: str, slot: int) -> bytes:
    """Scan one filter position (`24`) and return its FILTER_RESULT reply.

    Status until the data is ready (the reader may still be busy), SPECTRAL_DATA
    (`05 11`, not decoded) then FILTER_RESULT (`05 1b`), then status until idle. Any failure, a
    timeout or a cancelled call stops the scan (`0b 00`) first.

    Args:
      slide: "excitation", "dichroic" or "emission".
      slot: the filter position on that slide, from 1.

    Raises:
      TimeoutError: if the position takes longer than its timeout (`_FILTER_SCAN_TIMEOUT`,
        `_EMISSION_FILTER_SCAN_TIMEOUT` for emission).
      RuntimeError: if the scan ends without reporting its data ready.
    """
    timeout = (
      self._EMISSION_FILTER_SCAN_TIMEOUT if slide == "emission" else self._FILTER_SCAN_TIMEOUT
    )
    parameters = self._compute_filter_scan_parameters(slide, slot)
    driver = self._driver
    await driver.send_command(driver.CommandFamily.FILTER_SCAN, parameters=parameters)
    start = time.monotonic()
    try:
      await asyncio.sleep(driver.default_poll_interval)
      while True:
        status = await driver.send_command(driver.CommandFamily.STATUS, retries=5)
        if len(status) > 2 and status[2] & self._DATA_READY:
          break
        if not driver._parse_status(status).busy:
          raise RuntimeError(f"the {slide} filter scan ended without reporting its data ready")
        if time.monotonic() - start >= timeout:
          raise TimeoutError(f"the {slide} filter scan took longer than {timeout} s")
        await asyncio.sleep(driver.default_poll_interval)
      await driver.send_command(
        driver.CommandFamily.REQUEST,
        driver.Command.SPECTRAL_DATA,
        parameters=bytes(5),
        when_idle=False,
      )
      reply = await driver.send_command(
        driver.CommandFamily.REQUEST,
        driver.Command.FILTER_RESULT,
        parameters=bytes(5),
        when_idle=False,
      )
      await driver._wait_until_ready(
        timeout=max(timeout - (time.monotonic() - start), 0),
        poll_interval=driver.default_poll_interval,
      )
      return reply
    except BaseException:
      await self._drain_and_stop(f"{slide} filter scan")
      raise

  async def detect_all_filters(
    self,
  ) -> Tuple[ExcitationFilterSlide, DichroicFilterSlide, EmissionFilterSlide]:
    """Detect the filters in every position.

    - One scan each: excitation 1 to 4, dichroic A to C, emission 5 to 8 (slots 1 to 4).
    - Excitation and dichroic positions take about 8 s each, emission about 49 s: about 4 minutes.
    - Detected filters have no name, their edges in nm as the reader states them; an empty
      position holds none.
    - The slides replace `excitation_filter_slide`, `dichroic_filter_slide` and
      `emission_filter_slide` once every position is scanned.

    Returns:
      the excitation, dichroic and emission filter slides.

    Raises:
      RuntimeError: if the reader reports no spectrometer (absorbance), or no extended-red
        photomultiplier for the emission scans, whose limits are verified only with one; if a
        scan ends without its data.
      TimeoutError: if a position takes longer than its timeout (`_FILTER_SCAN_TIMEOUT`,
        `_EMISSION_FILTER_SCAN_TIMEOUT` for emission).
      ValueError: if a FILTER_RESULT is not one of its position.
    """
    device = self._driver.configuration
    if device.spectrometer is False or device.absorbance_available is False:
      raise RuntimeError("the reader reports no spectrometer, which filter detection scans with")
    if device.extended_red_photomultiplier_tube is False:
      raise RuntimeError(
        "the reader reports no extended-red photomultiplier; the emission scans' limits are only "
        "verified with one"
      )
    excitation_slide = ExcitationFilterSlide(self.configuration.excitation_filter_slots)
    dichroic_slide = DichroicFilterSlide(self.configuration.dichroic_filter_slots)
    emission_slide = EmissionFilterSlide(self.configuration.emission_filter_slots)
    for slot in range(1, excitation_slide.maximum_slots + 1):
      reply = await self._detect_filter("excitation", slot)
      optical_filter = self._compute_optical_filter(
        slot, self._decode_filter_result(reply, "excitation")
      )
      if optical_filter is not None:
        excitation_slide.register(optical_filter)
    for slot in range(1, dichroic_slide.maximum_slots + 1):
      reply = await self._detect_filter("dichroic", slot)
      dichroic_filter = self._compute_dichroic_filter(
        slot, self._decode_filter_result(reply, "dichroic")
      )
      if dichroic_filter is not None:
        dichroic_slide.register(dichroic_filter)
    for slot in range(1, emission_slide.maximum_slots + 1):
      reply = await self._detect_filter("emission", slot)
      optical_filter = self._compute_optical_filter(
        slot, self._decode_filter_result(reply, "emission")
      )
      if optical_filter is not None:
        emission_slide.register(optical_filter)
    logger.info("filter detection: %r, %r, %r", excitation_slide, dichroic_slide, emission_slide)
    self.excitation_filter_slide = excitation_slide
    self.dichroic_filter_slide = dichroic_slide
    self.emission_filter_slide = emission_slide
    return excitation_slide, dichroic_slide, emission_slide

  # ----------------------------------------
  # Z-profiles
  # ----------------------------------------

  # The gain definition bytes of `09`, the z-scan.
  _Z_PROFILE_GAIN_DEFINITION = bytes([0x04, 0x08])
  # The step between focal heights, in 0.01 mm: the only one verified.
  _Z_PROFILE_RESOLUTION = 10
  # Before the optics, as the end of a RUN's fluorescence channel header.
  _Z_PROFILE_OPTICS_HEADER = bytes.fromhex("0000000c")
  # Status reply byte 2 bit 5: the z-profile is ready.
  _Z_PROFILE_RESULT_READY = 0x20

  # -- checks --------------------------------------------------------------------------------------

  def _check_z_profile_arguments(
    self,
    fluorescence_channel: FluorescenceChannel,
    maximum_focal_height: float,
    gain_target_percent: Optional[float],
    *,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise if the fluorescence channel, maximum focal height, target or timeout is out of range.

    Raises:
      ValueError: listing every violation: the fluorescence channel's
        (`_check_fluorescence_channel`), a maximum focal height not in 0.1 mm steps above 0 within
        `focal_height_range` (the reader refuses 0), a target not above 0 up to 100 %, a timeout
        not positive.
    """
    errors = self._check_fluorescence_channel(fluorescence_channel)
    lowest, highest = self.configuration.focal_height_range
    if (
      not lowest < maximum_focal_height <= highest
      or not maximum_focal_height > 0
      or abs(maximum_focal_height * 10 - round(maximum_focal_height * 10)) > 1e-9
    ):
      errors.append(
        f"maximum_focal_height must be above {max(lowest, 0)} up to {highest} mm in 0.1 mm "
        f"steps, got {maximum_focal_height}"
      )
    if gain_target_percent is not None and not 0 < gain_target_percent <= 100:
      errors.append(f"gain_target_percent must be above 0 up to 100, got {gain_target_percent}")
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    if errors:
      raise ValueError("; ".join(errors))

  # -- `09` parameters -----------------------------------------------------------------------------

  def _compute_z_profile_parameters(
    self,
    plate: Plate,
    well: Well,
    fluorescence_channel: FluorescenceChannel,
    maximum_focal_height: float,
    gain_target_percent: Optional[float],
  ) -> bytes:
    """`09`'s 42 parameter bytes.

    The plate's geometry, the well's column and row (from 1), the gain definition, the resolution,
    `maximum_focal_height`, the target in 0.01 % (0 without), then the optics:
    `_Z_PROFILE_OPTICS_HEADER` and a RUN fluorescence channel's bytes without its gain and
    closing `00` (`_encode_optics`).
    """
    index = next(i for i, item in enumerate(plate.get_all_items()) if item is well)
    rows = plate.num_items_y
    target = 0 if gain_target_percent is None else round(gain_target_percent * 100)
    optics = self._encode_optics(
      *self._compute_optics(fluorescence_channel),
      *self._compute_wavelength_selectors(fluorescence_channel),
    )
    return (
      self._encode_plate_geometry(plate)
      + bytes([index // rows + 1, index % rows + 1])
      + self._Z_PROFILE_GAIN_DEFINITION
      + self._Z_PROFILE_RESOLUTION.to_bytes(2, "big")
      + round(maximum_focal_height * 100).to_bytes(2, "big")
      + target.to_bytes(2, "big")
      + self._Z_PROFILE_OPTICS_HEADER
      + optics
    )

  # -- decoding ------------------------------------------------------------------------------------

  @staticmethod
  def _decode_z_profile_result(
    reply: bytes, plate: Plate, *, start_timestamp: datetime, end_timestamp: datetime
  ) -> CLARIOstarZProfileResult:
    """The z-profile of a FOCUS_RESULT (`05 05`) reply.

    - Bytes 7-8: the gain; 9-12 the peak counts (u32 BE); 13-14 their percent of the detector's
      range in 0.01 %.
    - Bytes 15 and 16: the well's column and row (from 1).
    - Bytes 17-18: the peak focal height in 0.01 mm (s16 BE); 19-22 the z-profile's average
      around it.
    - Bytes 23-24: the point count n, then n points (s32 BE height in 0.01 mm, which can be below
      0; u32 BE counts).
    - Gain, peak counts and percent are 0 without a target.

    Raises:
      ValueError: if the reply is cut short or names a well not on the plate.
    """
    if len(reply) < 25:
      raise ValueError(f"FOCUS_RESULT reply too short, got {len(reply)} bytes")
    points = int.from_bytes(reply[23:25], "big")
    if len(reply) < 25 + 8 * points:
      raise ValueError(f"FOCUS_RESULT reply cut short: {points} points, got {len(reply)} bytes")
    column, row = reply[15], reply[16]
    if not (1 <= column <= plate.num_items_x and 1 <= row <= plate.num_items_y):
      raise ValueError(
        f"FOCUS_RESULT reply names a well not on the plate, got column {column}, row {row}"
      )
    well = plate.get_all_items()[(column - 1) * plate.num_items_y + row - 1]
    gain = int.from_bytes(reply[7:9], "big")
    peak_counts = int.from_bytes(reply[9:13], "big")
    percent = int.from_bytes(reply[13:15], "big")
    z_profile = [
      (
        int.from_bytes(reply[offset : offset + 4], "big", signed=True) / 100,
        int.from_bytes(reply[offset + 4 : offset + 8], "big"),
      )
      for offset in range(25, 25 + 8 * points, 8)
    ]
    return CLARIOstarZProfileResult(
      peak_focal_height=int.from_bytes(reply[17:19], "big", signed=True) / 100,
      gain=gain or None,
      well=plate.get_child_identifier(well),
      peak_counts=peak_counts or None,
      peak_detector_percent=percent / 100 if percent else None,
      average_signal=int.from_bytes(reply[19:23], "big"),
      z_profile=z_profile,
      # TODO(device): no z-scan with the temperature controller on is verified.
      temperature=None,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )

  # -- measuring -----------------------------------------------------------------------------------

  async def _run_z_profile(self, parameters: bytes, timeout: float) -> bytes:
    """Send a z-scan (`09`) and return its FOCUS_RESULT reply.

    As `_run_measurement`: status until the reader is idle, its stage at status byte 7 and the
    gain it tries at 9-10; the result must be ready (`_Z_PROFILE_RESULT_READY`), then
    `05 05 00 00 00 00 00`. Any failure, a timeout or a cancelled call stops it (`0b 00`) first.

    Raises:
      TimeoutError: if the z-scan takes longer than `timeout` seconds.
      RuntimeError: if it ends without reporting its result ready.
    """
    driver = self._driver
    await driver.send_command(driver.CommandFamily.FOCUS_WELL, parameters=parameters)
    start = time.monotonic()
    stage: Optional[int] = None
    try:
      await asyncio.sleep(driver.default_poll_interval)
      while True:
        status = await driver.send_command(driver.CommandFamily.STATUS, retries=5)
        if not driver._parse_status(status).busy:
          break
        if len(status) > 10 and status[7] != stage:
          stage = status[7]
          logger.debug("z-scan: stage %d, gain %d", stage, int.from_bytes(status[9:11], "big"))
        if time.monotonic() - start >= timeout:
          raise TimeoutError(f"the z-scan took longer than {timeout} s")
        await asyncio.sleep(driver.default_poll_interval)
      if not (len(status) > 2 and status[2] & self._Z_PROFILE_RESULT_READY):
        raise RuntimeError("the z-scan ended without reporting its result ready")
      return await driver.send_command(
        driver.CommandFamily.REQUEST,
        driver.Command.FOCUS_RESULT,
        parameters=bytes(5),
        when_idle=False,
      )
    except BaseException:
      await self._drain_and_stop("z-scan")
      raise

  async def measure_z_profile(
    self,
    fluorescence_channel: FluorescenceChannel,
    *,
    well: Well,
    gain_target_percent: Optional[float] = None,
    maximum_focal_height: float = 15.0,
    timeout: Optional[float] = None,
  ) -> CLARIOstarZProfileResult:
    """Measure the z-profile of `well` from the top and, with a target, fit the gain at its peak.

    - The reader reads the well from `maximum_focal_height` down in 0.1 mm steps to a lowest
      height it chooses (0.7 mm and -0.6 mm on hardware), and computes the peak (about 8 s).
    - With `gain_target_percent` it then fits the gain at the peak until the well reads that
      percent of the detector's 260,000 counts (about 14 s in all).
    - For the brightest of several wells, `measure` them first and pass the brightest.
    - Cancelling the call stops it on the reader.

    Args:
      fluorescence_channel: the optics, as for `measure` (`_check_fluorescence_channel`); a
        bandwidth None is the default bandwidth, a filter given as a slot or a name the slides'.
      well: the well to scan, on the plate in the plate holder.
      gain_target_percent: above 0 up to 100; None measures the z-profile only.
      maximum_focal_height: the height the scan starts at, mm above 0 in 0.1 mm steps within
        `configuration.focal_height_range`.
      timeout: seconds the z-scan may take; `default_read_timeout` when None.

    Returns:
      the z-profile, its peak, and the gain with a target.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the z-scan ends without a result.
      ValueError: if an argument is out of range (`_check_z_profile_arguments`, `_get_filter`),
        the well is not on the plate, or the reply is not a z-profile.
      TimeoutError: if the z-scan takes longer than `timeout` seconds.
    """
    filled = self._get_filled_fluorescence_channel(fluorescence_channel)
    self._check_z_profile_arguments(
      filled, maximum_focal_height, gain_target_percent, timeout=timeout
    )
    plate, _ = self._get_plate_and_wells(MeasurementSettings(), None, [well])
    parameters = self._compute_z_profile_parameters(
      plate, well, filled, maximum_focal_height, gain_target_percent
    )
    logger.info(
      "z-profile: %s on %s of %s, gain target %s %%",
      self._describe_fluorescence_channel(filled),
      plate.get_child_identifier(well),
      plate.name,
      gain_target_percent,
    )
    start_timestamp = datetime.now()
    reply = await self._run_z_profile(
      parameters, self.default_read_timeout if timeout is None else timeout
    )
    return self._decode_z_profile_result(
      reply, plate, start_timestamp=start_timestamp, end_timestamp=datetime.now()
    )

  # ----------------------------------------
  # Grids
  # ----------------------------------------

  # `07`'s first byte: the scan of a geometry's first and last well, the only one verified.
  _GRID_SCAN = 0x02
  # The detection byte: top or bottom optic; its high nibble, the corner mode, stays 0.
  _GRID_DETECTION: Dict[str, int] = {"top": 0x00, "bottom": 0x01}
  # The gain target of the z-scan before a scan, in percent: 50 before every scan on hardware.
  _GRID_GAIN_TARGET_PERCENT = 50.0
  # A grid reply's counts, u32 BE from this byte.
  _GRID_COUNTS_START = 34
  # The plate holder's footprint given points are placed on, mm (the SLAS plate footprint).
  _PLATE_HOLDER_SIZE = (127.76, 85.48)

  # -- checks --------------------------------------------------------------------------------------

  def _check_grid(
    self,
    fluorescence_channel: FluorescenceChannel,
    optic_position: str,
    gain: Optional[int],
    focal_height: Optional[float],
    timeout: Optional[float],
  ) -> List[str]:
    """The violations of a scan's fluorescence channel, optic position, gain, focal height and
    timeout; a gain or focal height None is not checked."""
    errors = self._check_fluorescence_channel(fluorescence_channel)
    errors += self._check_optic_position(optic_position)
    if gain is not None:
      errors += self._check_gain(gain, self.configuration.gain_range)
    if focal_height is not None:
      errors += self._check_focal_height(focal_height, self.configuration.focal_height_range)
    if timeout is not None and not timeout > 0:
      errors.append(f"timeout must be positive, got {timeout}")
    return errors

  # -- `07` parameters -----------------------------------------------------------------------------

  @staticmethod
  def _encode_grid_geometry(size: Tuple[float, float], points: Sequence[Coordinate]) -> bytes:
    """A made-up plate's 14 bytes around one or two points, as `_encode_plate_geometry`.

    Its first well at the first point and its last at the second (or the first again), with 2
    columns and 1 row (1 and 1 for one point); Y measured from the back edge.
    """
    first = points[0]
    last = points[-1]
    values = (size[0], size[1], first.x, size[1] - first.y, last.x, size[1] - last.y)
    block = b"".join(round(value * 100).to_bytes(2, "big") for value in values)
    return block + (bytes([2, 1]) if len(points) == 2 else bytes([1, 1]))

  def _compute_grid_parameters(
    self,
    geometry: bytes,
    fluorescence_channel: FluorescenceChannel,
    optic_position: str,
    focal_height: float,
    gain: int,
  ) -> bytes:
    """`07`'s 40 parameter bytes.

    `02`, the 14-byte geometry, the detection byte, `_Z_PROFILE_OPTICS_HEADER`, the optics
    (`_encode_optics`), the focal height in 0.01 mm and the gain (u16 BE), then `00 00` (the
    wavelength of an absorbance scan).
    """
    optics = self._encode_optics(
      *self._compute_optics(fluorescence_channel),
      *self._compute_wavelength_selectors(fluorescence_channel),
    )
    return (
      bytes([self._GRID_SCAN])
      + geometry
      + bytes([self._GRID_DETECTION[optic_position]])
      + self._Z_PROFILE_OPTICS_HEADER
      + optics
      + round(focal_height * 100).to_bytes(2, "big")
      + gain.to_bytes(2, "big")
      + bytes(2)
    )

  # -- decoding ------------------------------------------------------------------------------------

  @staticmethod
  def _decode_grid_layout(reply: bytes) -> Tuple[int, float]:
    """A scan's grid side and point spacing in mm, from its PLATE_MAP_CONFIG (`05 0d`) reply.

    Byte 6 the side, 7-8 the points (side squared), 9-10 the spacing in um, 11-12 the length of a
    mask of the points used (0: every point).

    Raises:
      ValueError: if the reply is cut short, or its points or mask are not a full square grid.
    """
    if len(reply) < 13:
      raise ValueError(f"PLATE_MAP_CONFIG reply too short, got {len(reply)} bytes")
    side = reply[6]
    points = int.from_bytes(reply[7:9], "big")
    if points != side * side or int.from_bytes(reply[11:13], "big"):
      raise ValueError(f"PLATE_MAP_CONFIG reply is not a full grid: side {side}, {points} points")
    return side, int.from_bytes(reply[9:11], "big") / 1000

  @classmethod
  def _decode_grid(cls, reply: bytes, side: int) -> Tuple[Tuple[int, int], List[List[int]]]:
    """A grid's well and counts, from its data reply.

    Bytes 7-8 the points, 23-24 and 25-26 the well's column and row (from 1, u16 BE), then the
    counts, u32 BE from byte 34, `side` to a row.

    Raises:
      ValueError: if the reply does not hold `side` squared counts.
    """
    points = side * side
    start = cls._GRID_COUNTS_START
    if len(reply) < start + 4 * points or int.from_bytes(reply[7:9], "big") != points:
      raise ValueError(f"grid reply does not hold {points} counts, got {len(reply)} bytes")
    counts = [int.from_bytes(reply[i : i + 4], "big") for i in range(start, start + 4 * points, 4)]
    well = (int.from_bytes(reply[23:25], "big"), int.from_bytes(reply[25:27], "big"))
    return well, [counts[row : row + side] for row in range(0, points, side)]

  @staticmethod
  def _decode_grid_centers(
    reply: bytes, size_y: float
  ) -> Tuple[Optional[Coordinate], Optional[Coordinate]]:
    """The first and last well's centres the reader found, from its PLATE_MAP_XY (`05 04`) reply.

    X1, Y1, Xn, Yn at bytes 8-15, u16 BE in 0.01 mm, Y from the back edge of a plate `size_y` mm
    deep; returned from the front edge. All zero: the reader found none.

    Raises:
      ValueError: if the reply is cut short.
    """
    if len(reply) < 16:
      raise ValueError(f"PLATE_MAP_XY reply too short, got {len(reply)} bytes")
    x1, y1, x2, y2 = (int.from_bytes(reply[i : i + 2], "big") / 100 for i in range(8, 16, 2))
    if not any((x1, y1, x2, y2)):
      return None, None
    return Coordinate(x1, size_y - y1, 0), Coordinate(x2, size_y - y2, 0)

  # -- measuring -----------------------------------------------------------------------------------

  async def _run_grid(
    self, parameters: bytes, timeout: float
  ) -> Tuple[int, float, List[bytes], bytes]:
    """Send a scan (`07`), collect its grids and the centres the reader found.

    PLATE_MAP_CONFIG right after the scan; then status until the reader is idle, a grid
    (`05 02`) each time status byte 2 bit 0 is set; once idle, PLATE_MAP_XY. Any failure, a
    timeout or a cancelled call stops it (`0b 00`) first.

    Returns:
      the grid side, the point spacing in mm, the grid replies and the PLATE_MAP_XY reply.

    Raises:
      TimeoutError: if the scan takes longer than `timeout` seconds.
    """
    driver = self._driver
    await driver.send_command(driver.CommandFamily.PLATE_MAP_SCAN, parameters=parameters)
    start = time.monotonic()
    grids: List[bytes] = []
    try:
      layout = await driver.send_command(
        driver.CommandFamily.REQUEST,
        driver.Command.PLATE_MAP_CONFIG,
        parameters=bytes(5),
        when_idle=False,
      )
      side, spacing = self._decode_grid_layout(layout)
      while True:
        await asyncio.sleep(driver.default_poll_interval)
        status = await driver.send_command(driver.CommandFamily.STATUS, retries=5)
        if len(status) > 2 and status[2] & self._DATA_READY:
          grids.append(await self._request_page(retries=1))
          continue
        if not driver._parse_status(status).busy:
          break
        if time.monotonic() - start >= timeout:
          raise TimeoutError(f"the scan took longer than {timeout} s")
      centers = await driver.send_command(
        driver.CommandFamily.REQUEST, driver.Command.PLATE_MAP_XY, parameters=bytes(5)
      )
    except BaseException:
      await self._drain_and_stop("scan")
      raise
    return side, spacing, grids, centers

  async def _get_grids(
    self,
    parameters: bytes,
    positions: Sequence[Coordinate],
    wells: Sequence[Optional[str]],
    size_y: float,
    focal_height: float,
    gain: int,
    timeout: Optional[float],
  ) -> List[CLARIOstarFluorescenceGridResult]:
    """Run one scan of one or two `positions` and return a grid for each, in order.

    A grid that repeats the well before it is dropped: the reader may send a one-point scan's
    point twice.

    Raises:
      RuntimeError: if the scan returns another number of grids than `positions`.
    """
    start_timestamp = datetime.now()
    side, spacing, replies, centers_reply = await self._run_grid(
      parameters, self.default_read_timeout if timeout is None else timeout
    )
    grids: List[List[List[int]]] = []
    previous: Optional[Tuple[int, int]] = None
    for reply in replies:
      well, counts = self._decode_grid(reply, side)
      if well != previous:
        grids.append(counts)
      previous = well
    if len(grids) != len(positions):
      raise RuntimeError(f"the scan returned {len(grids)} grids for {len(positions)} points")
    centers = self._decode_grid_centers(centers_reply, size_y)
    end_timestamp = datetime.now()
    return [
      CLARIOstarFluorescenceGridResult(
        position=position,
        well=well,
        center=center,
        spacing=spacing,
        counts=counts,
        focal_height=focal_height,
        gain=gain,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
      )
      for position, well, center, counts in zip(positions, wells, centers, grids)
    ]

  async def measure_grid(
    self,
    fluorescence_channel: FluorescenceChannel,
    *,
    positions: Sequence[Coordinate],
    gain: int,
    focal_height: float,
    optic_position: Optional[OpticPosition] = None,
    timeout: Optional[float] = None,
  ) -> List[CLARIOstarFluorescenceGridResult]:
    """Measure the signal over a grid of points around each of one or two `positions`.

    - The reader reads a 31 x 31 grid around each point, at one focal height and gain, and finds
      the signal's centre (about 50-80 s a point).
    - The points need not be wells: any point the optic reaches on the plate holder.
    - Cancelling the call stops it on the reader.

    Args:
      fluorescence_channel: the optics, as for `measure` (`_check_fluorescence_channel`).
      positions: one or two points, x and y in mm on the plate holder from its left-front corner.
      gain: the detector gain, within `configuration.gain_range`.
      focal_height: mm, in 0.1 mm steps within `configuration.focal_height_range`.
      optic_position: "top" or "bottom"; `default_optic_position` when None.
      timeout: seconds the scan may take; `default_read_timeout` when None.

    Returns:
      a grid for each point, in order.

    Raises:
      ValueError: if an argument is out of range, or a reply is not what the scan sends.
      TimeoutError: if the scan takes longer than `timeout` seconds.
      RuntimeError: if the scan returns another number of grids than points.
    """
    filled = self._get_filled_fluorescence_channel(fluorescence_channel)
    optic = self.default_optic_position if optic_position is None else optic_position
    errors = self._check_grid(filled, optic, gain, focal_height, timeout)
    size_x, size_y = self._PLATE_HOLDER_SIZE
    if not 1 <= len(positions) <= 2:
      errors.append(f"positions must hold one or two points, got {len(positions)}")
    for point in positions:
      if not (0 <= point.x <= size_x and 0 <= point.y <= size_y):
        errors.append(
          f"positions must lie on the plate holder (0-{size_x}, 0-{size_y}), got {point}"
        )
    if errors:
      raise ValueError("; ".join(errors))
    self._warn_untested("a grid at given positions")
    geometry = self._encode_grid_geometry(self._PLATE_HOLDER_SIZE, positions)
    parameters = self._compute_grid_parameters(geometry, filled, optic, focal_height, gain)
    logger.info("grid: %s at %s", self._describe_fluorescence_channel(filled), positions)
    return await self._get_grids(
      parameters, positions, [None] * len(positions), size_y, focal_height, gain, timeout
    )

  async def measure_well_centers(
    self,
    fluorescence_channel: FluorescenceChannel,
    *,
    wells: Optional[Sequence[Well]] = None,
    gain: Optional[int] = None,
    focal_height: Optional[float] = None,
    gain_target_percent: float = _GRID_GAIN_TARGET_PERCENT,
    optic_position: Optional[OpticPosition] = None,
    timeout: Optional[float] = None,
  ) -> List[CLARIOstarFluorescenceGridResult]:
    """Measure where the signal of each of `wells` is centred, from a grid of points around it.

    - Without `focal_height`, a z-scan of the first well (`measure_z_profile`) gives the focal
      height, and the gain too unless `gain` is given; every well is then read at both.
    - The wells are scanned two at a time (about 50-80 s a well). The plate's first and last well
      together are the scan the reader is verified with; other wells are untested.
    - Cancelling the call stops it on the reader.

    Args:
      fluorescence_channel: the optics, as for `measure` (`_check_fluorescence_channel`).
      wells: wells of the plate in the plate holder, any number; None for its first and last well.
      gain: the detector gain, within `configuration.gain_range`; None for the z-scan's, which
        needs `focal_height` None.
      focal_height: mm, in 0.1 mm steps within `configuration.focal_height_range`; None for the
        z-scan's peak, from the top optic only.
      gain_target_percent: the z-scan's gain target when it fits the gain, above 0 up to 100.
      optic_position: "top" or "bottom"; `default_optic_position` when None.
      timeout: seconds each scan (and the z-scan) may take; `default_read_timeout` when None.

    Returns:
      a grid for each well, in order, its centre and `center` relative to the plate.

    Raises:
      RuntimeError: if no plate is in the plate holder, the z-scan fits no gain, or a scan returns
        another number of grids than wells.
      ValueError: if an argument is out of range, a well is not on the plate, or a reply is not
        what the scan sends.
      TimeoutError: if a scan takes longer than `timeout` seconds.
    """
    filled = self._get_filled_fluorescence_channel(fluorescence_channel)
    optic = self.default_optic_position if optic_position is None else optic_position
    errors = self._check_grid(filled, optic, gain, focal_height, timeout)
    if focal_height is not None and gain is None:
      errors.append("give a gain with focal_height, or leave both to the z-scan")
    if focal_height is None and optic != "top":
      errors.append("the z-scan reads from the top: give focal_height for the bottom optic")
    if not 0 < gain_target_percent <= 100:
      errors.append(f"gain_target_percent must be above 0 up to 100, got {gain_target_percent}")
    if wells is not None and len(wells) == 0:
      errors.append("wells must hold at least one well")
    if errors:
      raise ValueError("; ".join(errors))
    plate, _ = self._get_plate_and_wells(
      MeasurementSettings(), None, None if wells is None else list(wells)
    )
    items = plate.get_all_items()
    targets = [items[0], items[-1]] if wells is None else list(wells)
    if focal_height is None:
      z_profile = await self.measure_z_profile(
        filled,
        well=targets[0],
        gain_target_percent=gain_target_percent if gain is None else None,
        timeout=timeout,
      )
      focal_height = z_profile.peak_focal_height
      gain = z_profile.gain if gain is None else gain
    if gain is None:
      raise RuntimeError("the z-scan fitted no gain")
    results: List[CLARIOstarFluorescenceGridResult] = []
    for i in range(0, len(targets), 2):
      pair = targets[i : i + 2]
      centres = [well.location + well.center() for well in pair if well.location is not None]
      if len(centres) != len(pair):
        raise ValueError(f"every well needs a location on {plate.name}")
      if pair == [items[0], items[-1]]:
        geometry = self._encode_plate_geometry(plate)
      else:
        self._warn_untested("a grid of wells other than the first and last")
        geometry = self._encode_grid_geometry((plate.get_size_x(), plate.get_size_y()), centres)
      parameters = self._compute_grid_parameters(geometry, filled, optic, focal_height, gain)
      identifiers: List[Optional[str]] = [plate.get_child_identifier(well) for well in pair]
      logger.info(
        "well centres: %s at %s of %s",
        self._describe_fluorescence_channel(filled),
        ", ".join(str(identifier) for identifier in identifiers),
        plate.name,
      )
      results += await self._get_grids(
        parameters, centres, identifiers, plate.get_size_y(), focal_height, gain, timeout
      )
    return results

  # ----------------------------------------
  # Discrete reads
  # ----------------------------------------

  # Flashes per well flying mode reads with (1 or 3).
  _FLYING_MODE_FLASHES = (1, 3)
  # Data reply layout byte [6], with the temperature controller on or off.
  _DATA_LAYOUT = 0xA1

  # -- checks --------------------------------------------------------------------------------------

  def _check_arguments(
    self,
    fluorescence_channels: Sequence[FluorescenceChannel],
    gains: Sequence[int],
    focal_height: Optional[float],
    measurement: MeasurementSettings,
    *,
    timeout: Optional[float] = None,
    optic_position: OpticPosition = "top",
    flying_mode: bool = False,
    enhanced_dynamic_range: bool = False,
  ) -> None:
    """Raise if the fluorescence channels, gains or read settings are out of range.

    The keywords are `measure`'s.

    Raises:
      ValueError: listing every violation: not 1 to `maximum_fluorescence_channels` fluorescence
        channels, not one gain per fluorescence channel, a fluorescence channel's
        (`_check_fluorescence_channel`, `_check_gain`; prefixed `fluorescence_channels[i]` when
        there are several), `_check_read`'s, `_check_optic_position`'s; flying mode with a well
        scan, with flashes other than
        `_FLYING_MODE_FLASHES`, with settling time or with enhanced dynamic range; enhanced
        dynamic range with orbital or spiral averaging.
    """
    maximum = self.configuration.maximum_fluorescence_channels
    count = len(fluorescence_channels)
    errors = (
      [] if 1 <= count <= maximum else [f"give 1 to {maximum} fluorescence channels, got {count}"]
    )
    if len(gains) != count:
      errors.append(
        f"give one gain per fluorescence channel, got {len(gains)} for {count} fluorescence "
        "channels"
      )
    for index, fluorescence_channel in enumerate(fluorescence_channels):
      fluorescence_channel_errors = self._check_fluorescence_channel(fluorescence_channel)
      if len(gains) == count:
        fluorescence_channel_errors += self._check_gain(gains[index], self.configuration.gain_range)
      if fluorescence_channel_errors:
        errors.append(
          (f"fluorescence_channels[{index}]: " if count > 1 else "")
          + "; ".join(fluorescence_channel_errors)
        )
    errors += self._check_read(focal_height, measurement, timeout)
    errors += self._check_optic_position(optic_position)
    if flying_mode:
      if measurement.well_scan != "point":
        errors.append(f"flying_mode reads each well at a point, got {measurement.well_scan!r}")
      if measurement.flashes not in self._FLYING_MODE_FLASHES:
        flashes = " or ".join(str(flashes) for flashes in self._FLYING_MODE_FLASHES)
        errors.append(f"flying_mode reads {flashes} flashes per well, got {measurement.flashes}")
      if measurement.settling_time != 0:
        errors.append(f"flying_mode reads without settling, got {measurement.settling_time} s")
      if enhanced_dynamic_range:
        errors.append(
          "flying_mode reads without enhanced_dynamic_range; set one of them, got both True"
        )
    if enhanced_dynamic_range and measurement.well_scan in ("orbital", "spiral"):
      errors.append(
        "enhanced_dynamic_range excludes orbital and spiral averaging, got "
        f"{measurement.well_scan!r}"
      )
    if errors:
      raise ValueError("; ".join(errors))

  # -- decoding and results ------------------------------------------------------------------------

  def _decode_data_reply(
    self, reply: bytes, fluorescence_channels: int, wells: int
  ) -> Tuple[List[List[List[int]]], int, Optional[float]]:
    """The counts by fluorescence channel, well (row by row) and point, ceiling, temperature.

    - Bytes 7-8 and 9-10: the values; 16-17 the fluorescence channels; 18-19 the wells; 21-22 the
      points per well (1, a 3 x 3 matrix's 9).
    - Bytes 11-14: the ceiling a saturated count reads (700,000,000 with enhanced dynamic range).
    - Bytes 32-33: the temperature in 0.1 C, 0 when not monitored.
    - From byte 34: u32 BE counts, by fluorescence channel, then by well, a well's points
      together (an absorbance matrix's layout; no fluorescence matrix of several wells verified).

    Raises:
      ValueError: if the reply is incomplete or not the layout of this read.
    """
    if len(reply) < 34:
      raise ValueError(f"fluorescence reply too short: {len(reply)} bytes")
    if reply[6] != self._DATA_LAYOUT:
      raise ValueError(f"fluorescence reply layout 0x{reply[6]:02x} is not decoded")
    announced = int.from_bytes(reply[7:9], "big")
    points = int.from_bytes(reply[21:23], "big")
    expected = fluorescence_channels * wells * points
    if (
      points < 1
      or int.from_bytes(reply[16:18], "big") != fluorescence_channels
      or int.from_bytes(reply[18:20], "big") != wells
      or announced != expected
    ):
      raise ValueError(
        f"fluorescence reply announces {announced} values; expected {fluorescence_channels} "
        f"fluorescence channel(s) of {wells} wells"
      )
    if int.from_bytes(reply[9:11], "big") != expected or len(reply) < 34 + 4 * expected:
      raise ValueError("fluorescence reply is incomplete")
    values = [int.from_bytes(reply[34 + 4 * i : 38 + 4 * i], "big") for i in range(expected)]
    counts = [
      [values[(c * wells + w) * points : (c * wells + w + 1) * points] for w in range(wells)]
      for c in range(fluorescence_channels)
    ]
    raw_temperature = int.from_bytes(reply[32:34], "big")
    temperature = raw_temperature / 10 if raw_temperature else None
    return counts, int.from_bytes(reply[11:15], "big"), temperature

  def _get_results(
    self,
    fluorescence_channels: Sequence[FluorescenceChannel],
    gains: Sequence[int],
    counts: List[List[List[int]]],
    identifiers: List[str],
    *,
    matrix: bool,
    temperature: Optional[float],
    start_timestamp: datetime,
    end_timestamp: datetime,
    focal_height: Optional[float] = None,
    optic_position: Optional[OpticPosition] = None,
  ) -> List[CLARIOstarFluorescenceResult]:
    """One result per fluorescence channel: each well's mean, and with `matrix` its points."""
    results = []
    for fluorescence_channel, gain, fluorescence_channel_counts in zip(
      fluorescence_channels, gains, counts
    ):
      points = {
        identifier: [float(value) for value in well]
        for identifier, well in zip(identifiers, fluorescence_channel_counts)
      }
      results.append(
        CLARIOstarFluorescenceResult(
          data={identifier: sum(values) / len(values) for identifier, values in points.items()},
          **self._get_result_optics(fluorescence_channel),
          gain=gain,
          temperature=temperature,
          start_timestamp=start_timestamp,
          end_timestamp=end_timestamp,
          matrix_points=points if matrix else None,
          focal_height=focal_height,
          optic_position=optic_position,
        )
      )
    return results

  # -- measuring -----------------------------------------------------------------------------------

  def _get_run_parameters(
    self,
    fluorescence_channels: Sequence[FluorescenceChannel],
    gains: Sequence[int],
    focal_height: Optional[float],
    measurement: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
    *,
    timeout: Optional[float],
    optic_position: OpticPosition,
    flying_mode: bool,
    enhanced_dynamic_range: bool,
  ) -> Tuple[Plate, List[Well], bytes]:
    """The plate, the wells and the RUN parameters of a read, every argument checked.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if an argument is out of range (`_check_arguments`), a matrix scan has several
        wells, or the settings cannot read the wells of the plate (`_get_plate_and_wells`).
    """
    self._check_arguments(
      fluorescence_channels,
      gains,
      focal_height,
      measurement,
      timeout=timeout,
      optic_position=optic_position,
      flying_mode=flying_mode,
      enhanced_dynamic_range=enhanced_dynamic_range,
    )
    plate, measured = self._get_plate_and_wells(measurement, shake, wells)
    if focal_height is None:
      focal_height = self._get_default_focal_height(measured)
    if measurement.well_scan == "matrix" and len(measured) > 1:
      # No fluorescence matrix of several wells is verified: its reply layout is unknown.
      raise ValueError(f"a fluorescence matrix scan reads one well, got {len(measured)}")
    parameters = self._compute_run_parameters(
      plate,
      measured,
      measurement,
      shake,
      focal_height=focal_height,
      kind=self._DISCRETE,
      count=len(fluorescence_channels),
      encoded_fluorescence_channels=[
        self._encode_fluorescence_channel(fluorescence_channel, gain)
        for fluorescence_channel, gain in zip(fluorescence_channels, gains)
      ],
      optics=self._OPTIC_POSITIONS[optic_position],
      flying_mode=flying_mode,
      enhanced_dynamic_range=enhanced_dynamic_range,
    )
    return plate, measured, parameters

  def _get_fluorescence_channels_and_gains(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    gain: Union[int, Sequence[int]],
  ) -> Tuple[List[FluorescenceChannel], List[int]]:
    """`measure`'s fluorescence channels, filled, and a gain for each.

    Each is filled by `_get_filled_fluorescence_channel`; one gain is given for all, or one each.
    """
    given = (
      [fluorescence_channels]
      if isinstance(fluorescence_channels, FluorescenceChannel)
      else list(fluorescence_channels)
    )
    filled = [
      self._get_filled_fluorescence_channel(fluorescence_channel) for fluorescence_channel in given
    ]
    gains = list(gain) if isinstance(gain, Sequence) else [gain] * len(filled)
    return filled, gains

  @overload
  async def measure(
    self,
    fluorescence_channels: FluorescenceChannel,
    *,
    gain: int,
    wells: Optional[List[Well]] = ...,
    focal_height: Optional[float] = ...,
    optic_position: Optional[OpticPosition] = ...,
    flying_mode: bool = ...,
    enhanced_dynamic_range: bool = ...,
    measurement_settings: Optional[MeasurementSettings] = ...,
    shake_settings: Optional[ShakeSettings] = ...,
    timeout: Optional[float] = ...,
  ) -> CLARIOstarFluorescenceResult: ...

  @overload
  async def measure(
    self,
    fluorescence_channels: Sequence[FluorescenceChannel],
    *,
    gain: Union[int, Sequence[int]],
    wells: Optional[List[Well]] = ...,
    focal_height: Optional[float] = ...,
    optic_position: Optional[OpticPosition] = ...,
    flying_mode: bool = ...,
    enhanced_dynamic_range: bool = ...,
    measurement_settings: Optional[MeasurementSettings] = ...,
    shake_settings: Optional[ShakeSettings] = ...,
    timeout: Optional[float] = ...,
  ) -> List[CLARIOstarFluorescenceResult]: ...

  async def measure(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    *,
    gain: Union[int, Sequence[int]],
    wells: Optional[List[Well]] = None,
    focal_height: Optional[float] = None,
    optic_position: Optional[OpticPosition] = None,
    flying_mode: bool = False,
    enhanced_dynamic_range: bool = False,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    timeout: Optional[float] = None,
  ) -> Union[CLARIOstarFluorescenceResult, List[CLARIOstarFluorescenceResult]]:
    """Measure the plate through one fluorescence channel, or several in one pass.

    The RUN frame holds the settings below once per read; per fluorescence channel it holds only
    the optics and the gain. Cancelling the call stops the run on the reader.

    Args:
      fluorescence_channels: one `FluorescenceChannel` (`pylabrobot.lib.fluorescence`), or 1 to
        `configuration.maximum_fluorescence_channels` of them, each within
        `_check_fluorescence_channel`; a bandwidth None is the default bandwidth, a filter given
        as a slot or a name the slides' (`detect_all_filters`).
      gain: the detector gain within `configuration.gain_range`: one for every fluorescence
        channel, or one per fluorescence channel in the order given.
      wells: the wells to measure, on the plate; None measures every well. A matrix scan reads
        one well (no fluorescence matrix of several wells is verified).
      focal_height: mm above the plate's base, within `configuration.focal_height_range`, in
        0.1 mm steps. None reads at `default_focal_height_percent_of_well_depth` (25: a quarter
        up the well, for low and moderate fills). For full wells, or the most signal, focus on a
        representative well with `measure_z_profile` and pass its `peak_focal_height`.
      optic_position: read from the top, or from the bottom if the reader has one; None for
        `default_optic_position`.
      flying_mode: read each well without stopping: point reads of 1 or 3 flashes with no
        settling time, no enhanced dynamic range.
      enhanced_dynamic_range: read counts up to 700 million instead of 260,000, not with
        orbital or spiral averaging; the gain is still sent. The counts are on another scale
        than a normal read's and not comparable with them.
      measurement_settings: how to read, without a pause; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      timeout: seconds the run may take; `default_read_timeout` when None.

    Returns:
      the counts of the measured wells, with the optics, gain, focal height and optic position they
      were read with; for a sequence of fluorescence channels, one result per fluorescence channel
      in the order given.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without its data.
      ValueError: if an argument is out of range (`_check_arguments`), a filter's slot or name has
        no slide or is not on it (`_get_filter`), a matrix scan has several wells, the settings
        cannot read the wells of the plate, or the reply is not this read's (`_decode_data_reply`).
      TimeoutError: if the run takes longer than `timeout` seconds.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    if optic_position is None:
      optic_position = self._default_optic_position
    filled, gains = self._get_fluorescence_channels_and_gains(fluorescence_channels, gain)
    plate, wells, parameters = self._get_run_parameters(
      filled,
      gains,
      focal_height,
      measurement,
      shake_settings,
      wells,
      timeout=timeout,
      optic_position=optic_position,
      flying_mode=flying_mode,
      enhanced_dynamic_range=enhanced_dynamic_range,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    if focal_height is None:
      focal_height = self._get_default_focal_height(wells)
    logger.info(
      "fluorescence: %s, %d of %d wells of %s, %s scan, %s optic",
      "; ".join(
        self._describe_fluorescence_channel(fluorescence_channel, gain)
        for fluorescence_channel, gain in zip(filled, gains)
      ),
      len(wells),
      plate.num_items,
      plate.name,
      measurement.well_scan,
      optic_position,
    )
    start_timestamp = datetime.now()
    replies, end_timestamp = await self._run_measurement(parameters, timeout)
    identifiers = _get_measured_identifiers(plate, wells)
    counts, ceiling, temperature = self._decode_data_reply(
      replies[0], len(filled), len(identifiers)
    )
    self._warn_saturated(
      "fluorescence",
      [value for fluorescence_channel in counts for well in fluorescence_channel for value in well],
      ceiling,
    )
    results = self._get_results(
      filled,
      gains,
      counts,
      identifiers,
      matrix=measurement.well_scan == "matrix",
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
      focal_height=focal_height,
      optic_position=optic_position,
    )
    return results[0] if isinstance(fluorescence_channels, FluorescenceChannel) else results

  # ----------------------------------------
  # Spectra
  # ----------------------------------------

  _SCANS = ("excitation", "emission")
  # A spectrum's scan byte: read well by well, in no reading direction.
  # A plate read's scan byte (`0a`) makes the reader send pages of another layout.
  _SPECTRUM_SCAN_BYTE = 0x00
  # A spectrum page's layout byte [6].
  _SPECTRUM_LAYOUT = 0xA0

  # -- checks --------------------------------------------------------------------------------------

  @staticmethod
  def _get_scanned_fluorescence_channel(
    fixed: FluorescenceChannel, scan: Scan, wavelength: int
  ) -> FluorescenceChannel:
    """A copy of a spectrum's `fixed` fluorescence channel with `scan` at `wavelength`."""
    if scan == "excitation":
      return replace(fixed, excitation_wavelength=wavelength)
    return replace(fixed, emission_wavelength=wavelength)

  def _check_spectrum_arguments(
    self,
    scan: Scan,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    fixed: FluorescenceChannel,
    gain: int,
    focal_height: Optional[float],
    measurement: MeasurementSettings,
    *,
    timeout: Optional[float] = None,
  ) -> None:
    """Raise if the spectrum's wavelengths, optics, gain or read settings are out of range.

    Raises:
      ValueError: listing every violation: an unknown scan, a wavelength in `fixed` for the
        scanned one, `fixed` as `_check_fluorescence_channel` refuses it, an end not a whole nm
        within `wavelength_range`, an end not above the start, a step other than 1 nm, more than
        `maximum_spectrum_wavelengths`, a fluorescence channel at either end that
        `_check_fluorescence_channel` refuses, `_check_gain`'s, `_check_read`'s, a
        matrix scan, a corner, reading direction or bidirectional setting other than the default.
    """
    errors = []
    if scan not in self._SCANS:
      errors.append(f"scan must be one of {list(self._SCANS)}, got {scan!r}")
    else:
      scanned = fixed.excitation_wavelength if scan == "excitation" else fixed.emission_wavelength
      if scanned is not None:
        errors.append(
          f"start_wavelength and end_wavelength replace {scan}_wavelength, got {scanned}"
        )
      errors += self._check_fluorescence_channel(fixed, scanned=scan)
    lowest, highest = self.configuration.wavelength_range
    ends_valid = True
    for name, value in (("start_wavelength", start_wavelength), ("end_wavelength", end_wavelength)):
      if not isinstance(value, int) or not lowest <= value <= highest:
        errors.append(f"{name} must be a whole {lowest} to {highest} nm, got {value!r}")
        ends_valid = False
    if step_size != 1:
      errors.append(f"step_size must be 1 nm, the only one verified, got {step_size!r}")
    if ends_valid:
      maximum = self.configuration.maximum_spectrum_wavelengths
      count = end_wavelength - start_wavelength + 1
      if end_wavelength <= start_wavelength:
        errors.append(f"end_wavelength must be above start_wavelength, got {end_wavelength}")
      elif count > maximum:
        errors.append(f"a spectrum holds at most {maximum} wavelengths, got {count}")
    if not errors:
      for wavelength in (start_wavelength, end_wavelength):
        at_wavelength = self._get_scanned_fluorescence_channel(fixed, scan, wavelength)
        fluorescence_channel_errors = self._check_fluorescence_channel(at_wavelength)
        if fluorescence_channel_errors:
          errors.append(f"at {wavelength} nm: " + "; ".join(fluorescence_channel_errors))
    errors += self._check_gain(gain, self.configuration.gain_range)
    errors += self._check_read(focal_height, measurement, timeout)
    if measurement.well_scan == "matrix":
      errors.append("a matrix well scan is not supported for a fluorescence spectrum")
    default = MeasurementSettings()
    directions: Tuple[Tuple[str, object, object], ...] = (
      ("corner", measurement.corner, default.corner),
      ("reading_direction", measurement.reading_direction, default.reading_direction),
      ("bidirectional", measurement.bidirectional, default.bidirectional),
    )
    for setting, given, default_setting in directions:
      if given != default_setting:
        errors.append(f"a fluorescence spectrum reads well by well; {setting} is not supported")
    if errors:
      raise ValueError("; ".join(errors))

  # -- decoding and results ------------------------------------------------------------------------

  def _decode_spectrum_pages(
    self, pages: List[bytes], plate: Plate, identifiers: List[str], wavelengths: int
  ) -> Tuple[Dict[str, List[int]], int, Optional[float]]:
    """Each well's counts by wavelength (in `identifiers`' order), the ceiling, the temperature.

    One page per well: the wavelengths at bytes 7-8 and 16-17, one well at 18-19, its column and
    row (from 1) at 23-24 and 25-26, the temperature at 32-33, u32 BE counts from byte 34. A
    page's well is its column and row, not its place in the order.

    Raises:
      ValueError: if a page is not a page of this read, or a well's page is missing or repeated.
    """
    items = plate.get_all_items()
    rows, columns = plate.num_items_y, plate.num_items_x
    spectra: Dict[str, List[int]] = {}
    for number, page in enumerate(pages, 1):
      if (
        len(page) < 34 + 4 * wavelengths
        or page[6] != self._SPECTRUM_LAYOUT
        or int.from_bytes(page[7:9], "big") != wavelengths
        or int.from_bytes(page[16:18], "big") != wavelengths
        or int.from_bytes(page[18:20], "big") != 1
      ):
        raise ValueError(f"fluorescence spectrum page {number} is not a page of this read")
      column = int.from_bytes(page[23:25], "big")
      row = int.from_bytes(page[25:27], "big")
      if not (1 <= column <= columns and 1 <= row <= rows):
        raise ValueError(f"fluorescence spectrum page {number} is of column {column}, row {row}")
      identifier = plate.get_child_identifier(items[(column - 1) * rows + row - 1])
      if identifier not in identifiers or identifier in spectra:
        raise ValueError(f"fluorescence spectrum page {number} is of {identifier}, not expected")
      spectra[identifier] = [
        int.from_bytes(page[34 + 4 * i : 38 + 4 * i], "big") for i in range(wavelengths)
      ]
    missing = [identifier for identifier in identifiers if identifier not in spectra]
    if missing:
      raise ValueError(f"fluorescence spectrum has no page for {missing}")
    raw_temperature = int.from_bytes(pages[0][32:34], "big")
    temperature = raw_temperature / 10 if raw_temperature else None
    ceiling = int.from_bytes(pages[0][11:15], "big")
    return {identifier: spectra[identifier] for identifier in identifiers}, ceiling, temperature

  def _get_spectrum_result(
    self,
    fixed: FluorescenceChannel,
    gain: int,
    wavelengths: List[int],
    spectra: Dict[str, List[int]],
    *,
    temperature: Optional[float],
    start_timestamp: datetime,
    end_timestamp: datetime,
  ) -> CLARIOstarFluorescenceSpectrumResult:
    """The spectrum's result: each well's counts by wavelength, with `fixed`'s optics."""
    return CLARIOstarFluorescenceSpectrumResult(
      data={
        identifier: {wavelength: float(count) for wavelength, count in zip(wavelengths, spectrum)}
        for identifier, spectrum in spectra.items()
      },
      **self._get_result_optics(fixed),
      gain=gain,
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )

  # -- measuring -----------------------------------------------------------------------------------

  async def measure_spectrum(
    self,
    *,
    scan: Scan,
    start_wavelength: int,
    end_wavelength: int,
    excitation_wavelength: Optional[float] = None,
    excitation_bandwidth: Optional[float] = None,
    emission_wavelength: Optional[float] = None,
    emission_bandwidth: Optional[float] = None,
    gain: int,
    wells: Optional[List[Well]] = None,
    step_size: int = 1,
    focal_height: Optional[float] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    timeout: Optional[float] = None,
  ) -> CLARIOstarFluorescenceSpectrumResult:
    """Measure a fluorescence spectrum: excitation or emission scanned, the other fixed.

    Both go through their monochromators, with the default dichroic split. The reader reads well
    by well, about 6 s per well for 101 wavelengths and 14 s for 221. Cancelling the call stops
    the run on the reader. A data page that arrives short ends the read, and the plate is measured
    once more.

    Args:
      scan: excitation or emission, scanned from `start_wavelength` to `end_wavelength`.
      start_wavelength: the first wavelength, a whole nm within `configuration.wavelength_range`.
      end_wavelength: the last wavelength, above `start_wavelength`, within the same range; at
        most `configuration.maximum_spectrum_wavelengths` wavelengths.
      excitation_wavelength: nm, the fixed excitation of an emission spectrum; None when
        excitation is scanned.
      excitation_bandwidth: nm; None for `default_excitation_bandwidth`.
      emission_wavelength: nm, the fixed emission of an excitation spectrum; None when emission is
        scanned.
      emission_bandwidth: nm; None for `default_emission_bandwidth`.
      gain: the detector gain within `configuration.gain_range`.
      wells: the wells to measure, on the plate; None measures every well.
      step_size: nm between wavelengths: 1, the only one verified.
      focal_height: mm above the plate's base, within `configuration.focal_height_range`, in
        0.1 mm steps. None reads at `default_focal_height_percent_of_well_depth` (25: a quarter
        up the well, for low and moderate fills). For full wells, or the most signal, focus on a
        representative well with `measure_z_profile` and pass its `peak_focal_height`.
      measurement_settings: how to read, without a pause, a matrix or another corner, reading
        direction or bidirectional setting; None reads with the defaults.
      shake_settings: shake the plate before the read; None does not shake.
      timeout: seconds the run may take; `default_read_timeout` when None, too short for a full
        plate of a long spectrum (14 s per well at 221 wavelengths).

    Returns:
      each measured well's counts by the scanned wavelength, with the fixed one's optics and the
      gain.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without all its data.
      ValueError: if an argument is out of range (`_check_spectrum_arguments`), the settings
        cannot read the wells of the plate, or the data pages are not this read's.
      TimeoutError: if the run takes longer than `timeout` seconds.
      FrameError: if a data page arrives short on the second read too.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    fixed = self._get_filled_fluorescence_channel(
      FluorescenceChannel(
        excitation_wavelength=excitation_wavelength,
        excitation_bandwidth=excitation_bandwidth,
        emission_wavelength=emission_wavelength,
        emission_bandwidth=emission_bandwidth,
      )
    )
    self._check_spectrum_arguments(
      scan,
      start_wavelength,
      end_wavelength,
      step_size,
      fixed,
      gain,
      focal_height,
      measurement,
      timeout=timeout,
    )
    plate, wells = self._get_plate_and_wells(measurement, shake_settings, wells)
    if focal_height is None:
      focal_height = self._get_default_focal_height(wells)
    start = self._get_scanned_fluorescence_channel(fixed, scan, start_wavelength)
    end = self._get_scanned_fluorescence_channel(fixed, scan, end_wavelength)
    wavelengths = list(range(start_wavelength, end_wavelength + 1))
    # The scanned monochromator's selector 02, the fixed one's 03, emission then excitation.
    scanned = self._WAVELENGTH_SELECTORS["scanned_monochromator"]
    fixed_selector = self._WAVELENGTH_SELECTORS["fixed_monochromator"]
    selectors = (fixed_selector, scanned) if scan == "excitation" else (scanned, fixed_selector)
    parameters = self._compute_run_parameters(
      plate,
      wells,
      measurement,
      shake_settings,
      focal_height=focal_height,
      kind=self._SPECTRUM,
      count=len(wavelengths),
      encoded_fluorescence_channels=[
        self._encode_fluorescence_channel(start, gain, selectors),
        self._encode_fluorescence_channel(end, gain, selectors),
      ],
      scan_byte=self._SPECTRUM_SCAN_BYTE,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
    if measurement.well_scan != "point":
      self._warn_untested(f"a fluorescence spectrum with {measurement.well_scan} averaging")
    identifiers = _get_measured_identifiers(plate, wells)
    logger.info(
      "fluorescence spectrum: %s %d to %d nm, %s, %d of %d wells of %s, %s scan",
      scan,
      start_wavelength,
      end_wavelength,
      self._describe_fluorescence_channel(start, gain),
      len(identifiers),
      plate.num_items,
      plate.name,
      measurement.well_scan,
    )
    try:
      start_timestamp = datetime.now()
      pages, end_timestamp = await self._run_measurement(
        parameters, timeout, progressive_pages=len(identifiers)
      )
    except FrameError as error:  # a page arrived short; the reader cannot send it again
      logger.warning("fluorescence spectrum: %s; measuring the plate again", error)
      start_timestamp = datetime.now()
      pages, end_timestamp = await self._run_measurement(
        parameters, timeout, progressive_pages=len(identifiers)
      )
    spectra, ceiling, temperature = self._decode_spectrum_pages(
      pages, plate, identifiers, len(wavelengths)
    )
    self._warn_saturated(
      "fluorescence", [value for spectrum in spectra.values() for value in spectrum], ceiling
    )
    return self._get_spectrum_result(
      fixed,
      gain,
      wavelengths,
      spectra,
      temperature=temperature,
      start_timestamp=start_timestamp,
      end_timestamp=end_timestamp,
    )
