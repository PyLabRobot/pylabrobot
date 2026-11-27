"""A simulated VANTAstar: the driver above the wire, answering from state and a recording."""

import logging
import os
import random
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union, overload

from pylabrobot.io.usb import USB
from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.resources import Plate, Well

from .configuration import VANTAstarConfiguration, configuration_from_json, read_configuration
from .features.absorbance import Absorbance
from .features.fluorescence import Fluorescence, VANTAstarZProfileResult
from .features.luminescence import Luminescence
from .features.measurement import (
  DEFAULT_APERTURE_DISTANCE,
  LUMINESCENCE_NO_FILTER,
  MeasurementSettings,
  MonochromatorSettings,
  ShakeSettings,
  _get_identifier,
)
from .features.temperature_controller import TemperatureController
from .master import VANTAstarDriver, VANTAstarStatus
from .responses import GainFocus

logger = logging.getLogger(__name__)

# How a simulated reader names its link in the logs, as the CLARIOstar's simulator does.
SIMULATED_LINK = "[simulation]"

# Optical densities a simulated well reads: up to 3.5, where Voyager caps its exports.
_OPTICAL_DENSITY_RANGE = (0.0, 3.5)
_OPTICAL_DENSITY_RESOLUTION = 3  # decimals: 0.001 OD
# The ceilings the tested reader states in its fluorescence data: without and with enhanced
# dynamic range.  No luminescence ceiling is known (no reader with luminescence was tested); a
# simulated luminescence read stays within the fluorescence ceiling.
_FLUORESCENCE_CEILING = 260_000
_ENHANCED_DYNAMIC_RANGE_CEILING = 750_000_000


def _get_identifiers(measured: List[Tuple[int, int]]) -> List[str]:
  """The identifiers of the measured wells (row, column), row by row."""
  return [_get_identifier(row, column) for row, column in measured]


def _get_values(identifiers: List[str], values: Dict[str, float], what: str) -> Dict[str, float]:
  """The values of ``identifiers`` in ``values``.

  Raises:
    ValueError: if ``values`` has no value for a measured well.
  """
  missing = [identifier for identifier in identifiers if identifier not in values]
  if missing:
    raise ValueError(f"{what} has no value for {missing}")
  return {identifier: float(values[identifier]) for identifier in identifiers}


async def _check_drawer_in(driver: VANTAstarDriver) -> Optional[float]:
  """Raise if the drawer is out, as a read does; return the temperature a read reports.

  Raises:
    RuntimeError: if the drawer is open.
  """
  status = await driver.request_status()
  if status.drawer_open:
    raise RuntimeError("the drawer is open; close it first")
  return status.temperatures[0] if status.temperatures is not None else None


class VANTAstarSimulationTemperatureController(TemperatureController):
  """The temperature controller of a simulated reader: it reaches its target at once.

  With no target there is no temperature to report: a simulated reader has no ambient.
  """

  async def measure_temperature(self, timeout: Optional[float] = None) -> float:
    """The target temperature.

    Raises:
      NotImplementedError: if no target is set.
    """
    if self.target_temperature is None:
      raise NotImplementedError("a simulated reader has no ambient temperature; set a target first")
    return self.target_temperature

  async def stop_temperature_control(self) -> None:
    """Stop heating; nothing is left to report."""
    await self._driver._set_incubator(0x02, 0)
    self.target_temperature = None
    logger.info("temperature control stopped")


class VANTAstarSimulationAbsorbance(Absorbance):
  """Absorbance on a simulated reader: each read is checked and encoded, nothing is sent; every
  well reads a random optical density, or its value in ``simulated_data``."""

  async def measure(
    self,
    wavelengths: Sequence[int],
    *,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[List[AbsorbanceResult]] = None,
    timeout: float = 300.0,
  ) -> List[AbsorbanceResult]:
    """Check and encode the read; every measured well reads a random optical density (0-3.5).

    The temperature is the temperature controller's target, as the simulated status reports it.

    Args:
      wavelengths, plate, wells, measurement_settings, shake_settings, timeout: as
        ``Absorbance.measure``; nothing shakes.
      simulated_data: results an earlier ``measure`` returned with the same wavelengths, on a
        reader or in simulation; each measured well reads its values there instead.

    Raises:
      RuntimeError: if no plate is on the plate holder, or the drawer is open.
      ValueError: if an argument is out of range, or ``simulated_data`` does not hold the same
        wavelengths, in the same order, with a value for every measured well.
    """
    plate, _, measured, _ = self._build_measure(wavelengths, plate, wells, measurement_settings)
    identifiers = _get_identifiers(measured)
    temperature = await _check_drawer_in(self._driver)
    timestamp = datetime.now()
    if simulated_data is None:
      logger.debug("%s read: random optical densities", SIMULATED_LINK)
      return [
        AbsorbanceResult(
          data={
            identifier: round(random.uniform(*_OPTICAL_DENSITY_RANGE), _OPTICAL_DENSITY_RESOLUTION)
            for identifier in identifiers
          },
          wavelength=wavelength,
          temperature=temperature,
          start_timestamp=timestamp,
          end_timestamp=timestamp,
        )
        for wavelength in wavelengths
      ]
    given = [result.wavelength for result in simulated_data]
    if given != list(wavelengths):
      raise ValueError(f"simulated_data holds {given}, measuring {list(wavelengths)}")
    logger.debug("%s read: simulated_data", SIMULATED_LINK)
    return [
      AbsorbanceResult(
        data=_get_values(identifiers, result.data, f"simulated_data at {result.wavelength} nm"),
        wavelength=result.wavelength,
        temperature=temperature,
        start_timestamp=timestamp,
        end_timestamp=timestamp,
      )
      for result in simulated_data
    ]

  async def measure_spectrum(
    self,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int = 1,
    *,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[AbsorbanceSpectrumResult] = None,
    timeout: float = 600.0,
  ) -> AbsorbanceSpectrumResult:
    """Check and encode the spectrum; every measured well reads a random optical density (0-3.5)
    at every wavelength.

    Args:
      start_wavelength, end_wavelength, step_size, plate, wells, measurement_settings,
        shake_settings, timeout: as ``Absorbance.measure_spectrum``; nothing shakes.
      simulated_data: a spectrum an earlier ``measure_spectrum`` returned, on a reader or in
        simulation; each measured well reads its spectrum there instead.

    Raises:
      RuntimeError: if no plate is on the plate holder, or the drawer is open.
      ValueError: if an argument is out of range, or ``simulated_data`` lacks a measured well at a
        wavelength.
    """
    _, _, measured, scanned, _ = self._build_spectrum(
      start_wavelength, end_wavelength, step_size, plate, wells, measurement_settings
    )
    identifiers = _get_identifiers(measured)
    temperature = await _check_drawer_in(self._driver)
    timestamp = datetime.now()
    if simulated_data is None:
      logger.debug("%s read: random optical densities", SIMULATED_LINK)
      data = {
        identifier: {
          wavelength: round(random.uniform(*_OPTICAL_DENSITY_RANGE), _OPTICAL_DENSITY_RESOLUTION)
          for wavelength in scanned
        }
        for identifier in identifiers
      }
    else:
      missing = [
        identifier
        for identifier in identifiers
        if any(wavelength not in simulated_data.data.get(identifier, {}) for wavelength in scanned)
      ]
      if missing:
        raise ValueError(f"simulated_data has no full spectrum for {missing}")
      logger.debug("%s read: simulated_data", SIMULATED_LINK)
      data = {
        identifier: {
          wavelength: float(simulated_data.data[identifier][wavelength]) for wavelength in scanned
        }
        for identifier in identifiers
      }
    return AbsorbanceSpectrumResult(
      data=data, temperature=temperature, start_timestamp=timestamp, end_timestamp=timestamp
    )


class VANTAstarSimulationFluorescence(Fluorescence):
  """Fluorescence on a simulated reader: each read is checked and encoded, nothing is sent; every
  well reads random counts within the detector's range, or its counts in ``simulated_data``.

  Spectra, the gain and focus adjustment and z-profiles are not simulated.
  """

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
    """Check and encode the read; every measured well reads random counts, up to 260,000 (with
    enhanced dynamic range 750,000,000), the ceilings the tested reader states.

    Args:
      fluorescence_channels, gain, wells, focal_height, enhanced_dynamic_range, monochromator,
        plate, measurement_settings, shake_settings, timeout: as ``Fluorescence.measure``;
        nothing shakes.
      simulated_data: results an earlier ``measure`` returned, one per fluorescence channel with
        its wavelengths and bandwidths, on a reader or in simulation; each measured well reads its
        counts there instead.

    Raises:
      RuntimeError: if no plate is on the plate holder, or the drawer is open.
      ValueError: as ``Fluorescence.measure`` for the arguments, or if ``simulated_data`` is not
        one result per channel with its optics and a value for every measured well.
    """
    _, _, measured, optics, _ = self._build_measure(
      fluorescence_channels,
      gain,
      monochromator,
      focal_height,
      enhanced_dynamic_range,
      plate,
      wells,
      measurement_settings,
    )
    identifiers = _get_identifiers(measured)
    temperature = await _check_drawer_in(self._driver)
    timestamp = datetime.now()
    if simulated_data is not None and len(simulated_data) != len(optics):
      raise ValueError(
        f"simulated_data holds {len(simulated_data)} results for {len(optics)} channels"
      )
    ceiling = _ENHANCED_DYNAMIC_RANGE_CEILING if enhanced_dynamic_range else _FLUORESCENCE_CEILING
    logger.debug(
      "%s read: %s", SIMULATED_LINK, "random counts" if simulated_data is None else "simulated_data"
    )
    results = []
    for index, (excitation, excitation_bandwidth, emission, emission_bandwidth, _) in enumerate(
      optics
    ):
      if simulated_data is None:
        data = {identifier: float(random.randint(0, ceiling)) for identifier in identifiers}
      else:
        result = simulated_data[index]
        what = f"simulated_data[{index}]"
        given = (
          result.excitation_wavelength,
          result.excitation_bandwidth,
          result.emission_wavelength,
          result.emission_bandwidth,
        )
        measuring = (excitation, excitation_bandwidth, emission, emission_bandwidth)
        if given != measuring:
          raise ValueError(f"{what} holds {given}, measuring {measuring}")
        data = _get_values(identifiers, result.data, what)
      results.append(
        FluorescenceResult(
          data=data,
          excitation_wavelength=excitation,
          excitation_bandwidth=excitation_bandwidth,
          emission_wavelength=emission,
          emission_bandwidth=emission_bandwidth,
          temperature=temperature,
          start_timestamp=timestamp,
          end_timestamp=timestamp,
        )
      )
    return results[0] if isinstance(fluorescence_channels, FluorescenceChannel) else results

  async def measure_spectrum(self, *args: Any, **kwargs: Any) -> FluorescenceSpectrumResult:
    """Raise: a fluorescence spectrum is not simulated."""
    raise NotImplementedError("a fluorescence spectrum is not simulated")

  async def adjust_gain_and_focus(self, *args: Any, **kwargs: Any) -> GainFocus:
    """Raise: the gain and focus adjustment is not simulated."""
    raise NotImplementedError("the gain and focus adjustment is not simulated")

  async def measure_z_profile(self, *args: Any, **kwargs: Any) -> VANTAstarZProfileResult:
    """Raise: a z-profile is not simulated."""
    raise NotImplementedError("a z-profile is not simulated")


class VANTAstarSimulationLuminescence(Luminescence):
  """Luminescence on a simulated reader: each read is checked and encoded, nothing is sent; every
  well reads random counts, or its counts in ``simulated_data``.  Not tested on a reader with
  luminescence, like the read it stands in for."""

  async def measure(
    self,
    *,
    integration_time: float = 0.8,
    gain: Optional[int] = None,
    aperture_distance: float = DEFAULT_APERTURE_DISTANCE,
    monochromator: MonochromatorSettings = LUMINESCENCE_NO_FILTER,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    simulated_data: Optional[LuminescenceResult] = None,
    timeout: float = 600.0,
  ) -> LuminescenceResult:
    """Check and encode the read; every measured well reads random counts, up to 260,000 (the
    fluorescence ceiling: no luminescence ceiling is known).

    Args:
      integration_time, gain, aperture_distance, monochromator, plate, wells,
        measurement_settings, shake_settings, timeout: as ``Luminescence.measure``; nothing
        shakes.
      simulated_data: a result an earlier ``measure`` returned, on a reader or in simulation; each
        measured well reads its counts there instead.

    Raises:
      RuntimeError: if no plate is on the plate holder, or the drawer is open.
      ValueError: as ``Luminescence.measure`` for the arguments, or if ``simulated_data`` has no
        value for a measured well.
    """
    _, _, measured, _ = self._build_measure(
      integration_time, gain, aperture_distance, monochromator, plate, wells, measurement_settings
    )
    identifiers = _get_identifiers(measured)
    temperature = await _check_drawer_in(self._driver)
    timestamp = datetime.now()
    if simulated_data is None:
      logger.debug("%s read: random counts", SIMULATED_LINK)
      data = {
        identifier: float(random.randint(0, _FLUORESCENCE_CEILING)) for identifier in identifiers
      }
    else:
      logger.debug("%s read: simulated_data", SIMULATED_LINK)
      data = _get_values(identifiers, simulated_data.data, "simulated_data")
    return LuminescenceResult(
      data=data,
      emission_wavelength=None,
      emission_bandwidth=None,
      temperature=temperature,
      start_timestamp=timestamp,
      end_timestamp=timestamp,
    )


class VANTAstarSimulationDriver(VANTAstarDriver):
  """A VANTAstar driver with no reader behind it.

  Nothing reaches a wire.  The configuration comes from ``declared_configuration_json``; status
  comes from the simulated state; the drawer moves, the shaker shakes and the temperature
  controller reaches its target at once.  Reads return random values or ``simulated_data``.
  """

  def __init__(self, declared_configuration_json: str, name: str = "VANTAstar"):
    """
    Args:
      declared_configuration_json: a file ``save_configuration`` wrote; the simulated reader's
        configuration.
      name: the device name, heading its log messages.
    """
    super().__init__(name=name, declared_configuration_json=declared_configuration_json)
    self.initialized = False
    self._drawer_open_state = False
    self.absorbance = VANTAstarSimulationAbsorbance(self)
    self.fluorescence = VANTAstarSimulationFluorescence(self)
    self.luminescence = VANTAstarSimulationLuminescence(self)
    self.temperature_controller = VANTAstarSimulationTemperatureController(self)

  def _build_io(self, device_id: Optional[str]) -> USB:
    """Build no link."""
    return None  # type: ignore[return-value]  # no link: nothing reaches a wire

  async def exchange(self, payload: bytes) -> bytes:
    """Raise: a simulated reader sends nothing."""
    raise NotImplementedError("a simulated reader sends nothing")

  async def send_command(self, *args: Any, **kwargs: Any) -> bytes:
    """Raise: a simulated reader sends nothing."""
    raise NotImplementedError("a simulated reader sends nothing")

  async def request_status(self, retries: int = 5) -> VANTAstarStatus:
    """The simulated state: never busy, the drawer where it was moved, a plate detected with the
    drawer in and a plate on the plate holder, the temperature controller's target as all three
    temperatures."""
    plate_holder = self.drawer.plate_holder
    target = self.temperature_controller.target_temperature
    return VANTAstarStatus(
      busy=False,
      plate_detected=(
        not self._drawer_open_state
        and plate_holder is not None
        and plate_holder.resource is not None
      ),
      drawer_open=self._drawer_open_state,
      motors_enabled=self.initialized,
      temperatures=None if target is None else (target, target, target),
    )

  async def _wait_until_ready(
    self,
    timeout: Optional[float],
    poll_interval: float,
    until: Optional[Callable[[VANTAstarStatus], bool]] = None,
  ) -> VANTAstarStatus:
    """Check the condition once against the simulated state; the reader is never busy.

    Raises:
      TimeoutError: if the condition does not hold.
    """
    status = await self.request_status()
    if until is not None and not until(status):
      raise TimeoutError(f"simulated VANTAstar status {status} does not meet the condition")
    return status

  async def _move_plate(self, mode: int) -> None:
    """Move the drawer at once."""
    logger.debug("%s write: drawer %s", SIMULATED_LINK, "out" if mode else "in")
    self._drawer_open_state = bool(mode)

  async def _idle_move(self, body: bytes) -> None:
    """Shake at once: nothing moves."""
    logger.debug("%s write: shake %s", SIMULATED_LINK, body.hex())

  async def _set_incubator(self, command_type: int, temperature: float) -> None:
    """Send nothing; the target is kept by the temperature controller."""
    logger.debug(
      "%s write: temperature controller %d %.1f", SIMULATED_LINK, command_type, temperature
    )

  async def estimate_duration(self, read: Callable[[], Awaitable[Any]]) -> int:
    """Raise: only a reader estimates a read."""
    raise NotImplementedError("a simulated reader estimates no run time")

  async def initialize(self) -> None:
    """Mark the reader initialized, motors on."""
    self.initialized = True

  def discover(self) -> VANTAstarConfiguration:
    """Read the configuration from ``declared_configuration_json``."""
    if self.declared_configuration_json is None:
      raise RuntimeError("a simulated reader needs a declared configuration")
    fields = read_configuration(self.declared_configuration_json)["device"]
    self.configuration = configuration_from_json(fields)
    return self.configuration

  def format_setup_summary(self) -> str:
    """One line describing the simulated reader and the configuration it answers from."""
    configuration = os.path.basename(self.declared_configuration_json or "no configuration")
    return f"[{self.name}] {SIMULATED_LINK} ({configuration})"

  async def setup(self) -> None:
    """Initialize and read the configuration, with nothing to connect."""
    await self.initialize()
    self.discover()
    logger.info("%s", self.format_setup_summary())

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Close the drawer and check for a plate left in, as a reader does; then mark the reader not
    initialized.  There is nothing to close.

    Raises:
      RuntimeError: if a plate is still in the reader, unless ``accept_plate_left_in_device``; the
        drawer is opened.
    """
    try:
      if self._drawer_open_state:
        await self.drawer.close()
      if not accept_plate_left_in_device and (await self.request_status()).plate_detected:
        await self.drawer.open()
        raise RuntimeError(
          "a plate is still in the reader; take it out, or pass accept_plate_left_in_device=True"
        )
    finally:
      self.initialized = False
