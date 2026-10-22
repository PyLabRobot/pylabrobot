"""CLARIOstar fluorescence: plate reads and spectra.

A plate read goes through one to five fluorescence channels in one pass (`FluorescenceChannel`, the
vendor's chromatics), from the top or the bottom optic. Excitation and emission each select their
wavelength through a monochromator or a filter. A spectrum scans the excitation or the emission
monochromator.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import (
  TYPE_CHECKING,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Tuple,
  Union,
  overload,
)

from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.resources import Plate, Well

from ..errors import FrameError
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
      the plain mean of each well's points; the vendor's own reduction is not verified (its
      export of a 3 x 3 matrix reads 141 where the mean is 141.78).
  """

  gain: int
  matrix_points: Optional[Dict[str, List[float]]] = None


@dataclass
class CLARIOstarFluorescenceSpectrumResult(FluorescenceSpectrumResult):
  """A CLARIOstar fluorescence spectrum, with the detector gain it was read with.

  Attributes:
    gain: the detector gain, within `FluorescenceConfiguration.gain_range`.
  """

  gain: int


@dataclass
class FluorescenceConfiguration(MeasurementConfiguration):
  """Device facts of the fluorescence measurement."""

  wavelength_range: Tuple[int, int] = (320, 840)
  """Excitation and emission wavelengths, in nm (the operating manual's monochromator range with
  an extended-red photomultiplier; up to 740 without, `Fluorescence._get_reader_configuration`)."""
  bandwidth_range: Tuple[int, int] = (8, 100)
  """Excitation and emission bandwidths, in nm (the operating manual)."""
  dichroic_range: Tuple[float, float] = (340.0, 760.0)
  """Where the dichroic mirror splits excitation from emission, in nm (the operating manual; up
  to 740 without an extended-red photomultiplier)."""
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
  """Filter positions on each of the two excitation or emission slides (filter detection)."""
  maximum_chromatics: int = 5
  """Fluorescence channels per read, the vendor's chromatics (the vendor software: up to 5)."""
  maximum_spectrum_wavelengths: int = 1000
  """Wavelengths per spectrum (the vendor software manual: up to 1000 measurement points)."""


# ----------------------------------------
# Wire values of the optics
# ----------------------------------------


def _compute_band_edges(wavelength: float, bandwidth: float) -> Tuple[int, int]:
  """The band's upper and lower edge in 0.1 nm: `wavelength` +/- half `bandwidth`.

  The nominal edges; the vendor software sends each band narrower (14.6 nm of 15).
  """
  return round((wavelength + bandwidth / 2) * 10), round((wavelength - bandwidth / 2) * 10)


def _compute_dichroic(excitation: Tuple[int, int], emission: Tuple[int, int]) -> int:
  """The default dichroic split in 0.1 nm, rounded half to even, as the vendor's.

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

  # Without an extended-red photomultiplier reading fluorescence, the vendor software refuses
  # excitation, emission or a dichroic split above 740 nm.
  _STANDARD_WAVELENGTH_RANGE = (320, 740)
  _STANDARD_DICHROIC_RANGE = (340.0, 740.0)

  @classmethod
  def _get_reader_configuration(cls, device: "DeviceConfiguration") -> FluorescenceConfiguration:
    """The fluorescence facts of the reader `device` describes: its wavelength limits.

    The extended range needs the extended-red photomultiplier on the fluorescence path: fitted,
    and on a dual-photomultiplier reader not crossed over. Unknown hardware keeps the defaults.
    """
    configuration = FluorescenceConfiguration()
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
    self._default_excitation_bandwidth = 15  # nm, the vendor's default
    self._default_emission_bandwidth = 20  # nm, the vendor's default
    self._default_optic_position: OpticPosition = "top"  # the vendor's default
    self._default_focal_height_percent_of_well_depth = 25.0

  # ----------------------------------------
  # Defaults
  # ----------------------------------------

  def _check_default_bandwidth(self, name: str, value: int) -> None:
    """Raise unless `value` is a bandwidth `_check_bandwidth` accepts."""
    errors = self._check_bandwidth(name, value, self.configuration.bandwidth_range)
    if errors:
      raise ValueError(errors[0])

  @property
  def default_excitation_bandwidth(self) -> int:
    """Excitation bandwidth, nm, of a monochromator read given none (the vendor's: 15)."""
    return self._default_excitation_bandwidth

  @default_excitation_bandwidth.setter
  def default_excitation_bandwidth(self, value: int) -> None:
    """Set the default, raising if `_check_bandwidth` refuses it."""
    self._check_default_bandwidth("default_excitation_bandwidth", value)
    self._default_excitation_bandwidth = value

  @property
  def default_emission_bandwidth(self) -> int:
    """Emission bandwidth, nm, of a monochromator read given none (the vendor's: 20)."""
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
    """The optic a read uses given none (the vendor's: top)."""
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

  def _get_fluorescence_channel_with_default_bandwidths(
    self, fluorescence_channel: FluorescenceChannel
  ) -> FluorescenceChannel:
    """A copy of `fluorescence_channel`, excitation and emission bandwidths given the default.

    Only excitation or emission without a filter or a bandwidth gets the default.
    """
    excitation_bandwidth = fluorescence_channel.excitation_bandwidth
    if excitation_bandwidth is None and fluorescence_channel.excitation_filter is None:
      excitation_bandwidth = self._default_excitation_bandwidth
    emission_bandwidth = fluorescence_channel.emission_bandwidth
    if emission_bandwidth is None and fluorescence_channel.emission_filter is None:
      emission_bandwidth = self._default_emission_bandwidth
    return replace(
      fluorescence_channel,
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
    `wavelength_range`, a bandwidth `_check_bandwidth` refuses, a filter slot outside its filter
    slots.

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
      # TODO(device): no read with excitation above emission is captured.
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
      fluorescence_channel: the fluorescence channel, its bandwidths filled.
      scanned: a spectrum's scanned excitation or emission, which has no wavelength.

    Returns:
      every rule broken: excitation or emission as `_check_excitation_or_emission_on_reader`
      refuses, an excitation or emission filter without a `dichroic_filter`, a split not in
      0.1 nm steps, a dichroic slot outside `dichroic_filter_slots`, `_check_bands`'s.
    """
    errors = self._check_excitation_or_emission_on_reader(
      "excitation",
      fluorescence_channel.excitation_wavelength,
      fluorescence_channel.excitation_bandwidth,
      fluorescence_channel.excitation_filter,
      scanned=scanned == "excitation",
    ) + self._check_excitation_or_emission_on_reader(
      "emission",
      fluorescence_channel.emission_wavelength,
      fluorescence_channel.emission_bandwidth,
      fluorescence_channel.emission_filter,
      scanned=scanned == "emission",
    )
    if fluorescence_channel.dichroic_filter is None and (
      fluorescence_channel.excitation_filter is not None
      or fluorescence_channel.emission_filter is not None
    ):
      # No read with an excitation or emission filter and the dichroic mirror is captured.
      errors.append("an excitation or emission filter needs a dichroic_filter")
    split = fluorescence_channel.dichroic_split_wavelength
    if split is not None and (
      not isinstance(split, (int, float)) or abs(split * 10 - round(split * 10)) > 1e-9
    ):
      errors.append(f"dichroic_split_wavelength must be nm in 0.1 nm steps, got {split!r}")
    if fluorescence_channel.dichroic_filter is not None:
      slots = self.configuration.dichroic_filter_slots
      slot = fluorescence_channel.dichroic_filter.slot
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
        dichroic_filter=fluorescence_channel.dichroic_filter,
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
  # Between two of the vendor's chromatics, one per fluorescence channel.
  _CHROMATIC_SEPARATOR = bytes.fromhex("00000c")
  # The excitation and emission wavelength selectors, after the emission edges: a plate read's
  # monochromator or filter; a spectrum's scanned and fixed monochromators.
  _WAVELENGTH_SELECTORS: Dict[str, int] = {
    "excitation_monochromator": 0x03,
    "emission_monochromator": 0x04,
    "filter": 0x01,
    "scanned_monochromator": 0x02,
    "fixed_monochromator": 0x03,
  }
  # Pause before reading: off. No fluorescence read with a pause is captured.
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
    excitation_wavelength = fluorescence_channel.excitation_wavelength
    excitation_bandwidth = fluorescence_channel.excitation_bandwidth
    emission_wavelength = fluorescence_channel.emission_wavelength
    emission_bandwidth = fluorescence_channel.emission_bandwidth
    if fluorescence_channel.excitation_filter is not None:
      excitation = _compute_filter_positions(fluorescence_channel.excitation_filter.slot, per_slide)
    elif excitation_wavelength is not None and excitation_bandwidth is not None:
      excitation = _compute_band_edges(excitation_wavelength, excitation_bandwidth)
    else:
      raise ValueError("give excitation_wavelength and excitation_bandwidth, or excitation_filter")
    if fluorescence_channel.emission_filter is not None:
      first, second = _compute_filter_positions(
        fluorescence_channel.emission_filter.slot, per_slide
      )
      emission = (second, first)
    elif emission_wavelength is not None and emission_bandwidth is not None:
      emission = _compute_band_edges(emission_wavelength, emission_bandwidth)
    else:
      raise ValueError("give emission_wavelength and emission_bandwidth, or emission_filter")
    if fluorescence_channel.dichroic_filter is not None:
      dichroic = fluorescence_channel.dichroic_filter.slot + 1
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
    """One fluorescence channel's 17 bytes, the vendor's chromatic: gain u16 BE, the optics, `00`.

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
    chromatics: Sequence[bytes],
    optics: int = 0,
    flying_mode: bool = False,
    enhanced_dynamic_range: bool = False,
    scan_byte: Optional[int] = None,
  ) -> bytes:
    """The RUN parameters of a fluorescence read or spectrum.

    The run start (`_compute_run_start`, with `optics`, `flying_mode`, `enhanced_dynamic_range`
    and `scan_byte`), the focal height in 0.01 mm, `kind`, `count` u16 BE and `00 00 00 00 00 0c`,
    the `chromatics` (`_encode_fluorescence_channel`), no pause, the run end.

    Args:
      kind: `_DISCRETE`, or `_SPECTRUM` with the start and end chromatics between which the reader
        steps.
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
      + self._CHROMATIC_SEPARATOR.join(chromatics)
      + self._NO_PAUSE
      + self._compute_run_end(measurement.flashes)
    )

  # -- the log and the results ---------------------------------------------------------------------

  def _describe_fluorescence_channel(
    self, fluorescence_channel: FluorescenceChannel, gain: int
  ) -> str:
    """One fluorescence channel of the read, for the log: excitation, emission and gain.

    Excitation and emission each as wavelength and bandwidth, or filter.
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
      fluorescence_channel.excitation_filter,
    )
    emission = describe(
      fluorescence_channel.emission_wavelength,
      fluorescence_channel.emission_bandwidth,
      fluorescence_channel.emission_filter,
    )
    return f"{excitation} -> {emission}, gain {gain}"

  def _get_result_optics(
    self, fluorescence_channel: FluorescenceChannel
  ) -> Dict[str, Optional[float]]:
    """A fluorescence channel's optics as a result's keyword arguments.

    Excitation's and emission's filter passband, or monochromator wavelength and the bandwidth the
    read used.
    """
    excitation_filter = fluorescence_channel.excitation_filter
    emission_filter = fluorescence_channel.emission_filter
    optics: Dict[str, Optional[float]] = {}
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
  # Discrete reads
  # ----------------------------------------

  # Flashes per well flying mode reads with (the vendor software manual: 1 or 3).
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
      ValueError: listing every violation: not 1 to `maximum_chromatics` fluorescence channels,
        not one gain per fluorescence channel, a fluorescence channel's
        (`_check_fluorescence_channel`, `_check_gain`; prefixed `fluorescence_channels[i]` when
        there are several), `_check_read`'s, `_check_optic_position`'s; flying mode with a well
        scan, with flashes other than
        `_FLYING_MODE_FLASHES`, with settling time or with enhanced dynamic range; enhanced
        dynamic range with orbital or spiral averaging.
    """
    maximum = self.configuration.maximum_chromatics
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
      together (an absorbance matrix's layout; no fluorescence matrix of several wells captured).

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
        )
      )
    return results

  # -- measuring -----------------------------------------------------------------------------------

  def _get_fluorescence_channels_and_gains(
    self,
    fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]],
    gain: Union[int, Sequence[int]],
  ) -> Tuple[List[FluorescenceChannel], List[int]]:
    """`measure`'s fluorescence channels, filled, and a gain for each.

    Each gets the default bandwidths (`_get_fluorescence_channel_with_default_bandwidths`); one gain
    is given for all, or one each.
    """
    given = (
      [fluorescence_channels]
      if isinstance(fluorescence_channels, FluorescenceChannel)
      else list(fluorescence_channels)
    )
    filled = [
      self._get_fluorescence_channel_with_default_bandwidths(fluorescence_channel)
      for fluorescence_channel in given
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
        `configuration.maximum_chromatics` of them, each within `_check_fluorescence_channel`; a
        bandwidth None is the default bandwidth.
      gain: the detector gain within `configuration.gain_range`: one for every fluorescence
        channel, or one per fluorescence channel in the order given.
      wells: the wells to measure, on the plate; None measures every well. A matrix scan reads
        one well (no fluorescence matrix of several wells is captured).
      focal_height: mm above the plate's base, within `configuration.focal_height_range`, in
        0.1 mm steps. None reads at `default_focal_height_percent_of_well_depth` (25: a quarter
        up the well, for low and moderate fills).
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
      the counts of the measured wells, with the optics and the gain they were read with; for a
      sequence of fluorescence channels, one result per fluorescence channel in the order given.

    Raises:
      RuntimeError: if no plate is in the plate holder, or the run ends without its data.
      ValueError: if an argument is out of range (`_check_arguments`), a matrix scan has several
        wells, the settings cannot read the wells of the plate, or the reply is not this read's
        (`_decode_data_reply`).
      TimeoutError: if the run takes longer than `timeout` seconds.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    if optic_position is None:
      optic_position = self._default_optic_position
    filled, gains = self._get_fluorescence_channels_and_gains(fluorescence_channels, gain)
    self._check_arguments(
      filled,
      gains,
      focal_height,
      measurement,
      timeout=timeout,
      optic_position=optic_position,
      flying_mode=flying_mode,
      enhanced_dynamic_range=enhanced_dynamic_range,
    )
    plate, wells = self._get_plate_and_wells(measurement, shake_settings, wells)
    if focal_height is None:
      focal_height = self._get_default_focal_height(wells)
    if measurement.well_scan == "matrix" and len(wells) > 1:
      # No fluorescence matrix of several wells is captured: its reply layout is unknown.
      raise ValueError(f"a fluorescence matrix scan reads one well, got {len(wells)}")
    parameters = self._compute_run_parameters(
      plate,
      wells,
      measurement,
      shake_settings,
      focal_height=focal_height,
      kind=self._DISCRETE,
      count=len(filled),
      chromatics=[
        self._encode_fluorescence_channel(fluorescence_channel, gain)
        for fluorescence_channel, gain in zip(filled, gains)
      ],
      optics=self._OPTIC_POSITIONS[optic_position],
      flying_mode=flying_mode,
      enhanced_dynamic_range=enhanced_dynamic_range,
    )
    timeout = self.default_read_timeout if timeout is None else timeout
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
    )
    return results[0] if isinstance(fluorescence_channels, FluorescenceChannel) else results

  # ----------------------------------------
  # Spectra
  # ----------------------------------------

  _SCANS = ("excitation", "emission")
  # A spectrum's scan byte: read well by well, in no reading direction, as the vendor sends it.
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
      errors.append(f"step_size must be 1 nm, the only one captured, got {step_size!r}")
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
      step_size: nm between wavelengths: 1, the only one captured.
      focal_height: mm above the plate's base, within `configuration.focal_height_range`, in
        0.1 mm steps. None reads at `default_focal_height_percent_of_well_depth` (25: a quarter
        up the well, for low and moderate fills).
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
    fixed = self._get_fluorescence_channel_with_default_bandwidths(
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
      chromatics=[
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
