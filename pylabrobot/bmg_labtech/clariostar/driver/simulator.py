"""A simulated CLARIOstar: the driver above the wire, answering from state and a recording."""

import logging
import os
import random
from datetime import datetime
from typing import Callable, Dict, List, Literal, Optional, Sequence, TypeVar, Union

from pylabrobot.io.ftdi import FTDI
from pylabrobot.io.usb import USB
from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.resources import Plate, Well

from .configuration import configuration_from_json, read_configuration
from .features.absorbance import (
  Absorbance,
  CLARIOstarAbsorbanceResult,
  CLARIOstarAbsorbanceSpectrumResult,
  Report,
  _get_spectrum_wavelengths,
)
from .features.drawer import Drawer
from .features.measurement import (
  MeasurementSettings,
  ShakeSettings,
  _get_measured_identifiers,
  _get_readings_by_well,
)
from .features.temperature_controller import TemperatureController
from .master import CLARIOstarDriver, CLARIOstarStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How a simulated reader names its link in the logs, as the STAR's and the Prep's simulators do.
SIMULATED_LINK = "[simulation]"

# The manufacturer's absorbance range and resolution, in OD.
_OPTICAL_DENSITY_RANGE = (0.0, 4.0)
_OPTICAL_DENSITY_RESOLUTION = 3  # decimals: 0.001 OD


def _require_read(value: Optional[T], name: str) -> T:
  """Return a configuration value, or raise if the simulated reader has not been set up."""
  if value is None:
    raise RuntimeError(f"{name} is not read yet; call `setup` first")
  return value


class CLARIOstarSimulationDrawer(Drawer):
  """The drawer of a simulated reader: a move puts the plate holder where the drawer goes at once.

  The resource model is where the drawer is. Until `location_in` and `location_out` are set, the
  model cannot hold a drawer position, and the last move is kept instead.
  """

  def __init__(self, driver: CLARIOstarDriver):
    """Start with the drawer in."""
    super().__init__(driver)
    self._last_move_open = False

  def _get_open(self) -> bool:
    """Whether the drawer is out, read from the resource model."""
    if self.plate_holder is None or self.location_in is None or self.location_out is None:
      return self._last_move_open
    return self.plate_holder.location == self.location_out

  def _get_plate_present(self) -> bool:
    """Whether a plate is in the reader: the drawer is in and a plate is in the plate holder."""
    return (
      self.plate_holder is not None
      and self.plate_holder.resource is not None
      and (not self._get_open())
    )

  async def _unchecked_fw_open(self) -> None:
    """Put the plate holder where the drawer is out."""
    logger.debug("%s write: drawer open", SIMULATED_LINK)
    self._last_move_open = True
    self._update_location(True)

  async def _unchecked_fw_close(self) -> None:
    """Put the plate holder where the drawer is in."""
    logger.debug("%s write: drawer close", SIMULATED_LINK)
    self._last_move_open = False
    self._update_location(False)


class CLARIOstarSimulationTemperatureController(TemperatureController):
  """The temperature controller of a simulated reader: it reaches its target at once.

  With no target there is no temperature to report: a simulated reader has no ambient.
  """

  async def _unchecked_fw_set(self, value: int) -> None:
    """Send nothing; the target is kept by the caller."""
    logger.debug("%s write: temperature controller %04x", SIMULATED_LINK, value)

  async def _warn_if_below_current(self, temperature: float) -> None:
    """Warn only against a target already set: a simulated reader has no ambient."""
    if self.target_temperature is not None:
      await super()._warn_if_below_current(temperature)

  async def measure_temperature(
    self, sensor: Literal["bottom", "top", "mean"] = "mean", timeout: Optional[float] = None
  ) -> float:
    """The target temperature, on both sensors.

    Raises:
      NotImplementedError: if no target is set.
    """
    if self.target_temperature is None:
      raise NotImplementedError("a simulated reader has no ambient temperature; set a target first")
    return self.target_temperature


class CLARIOstarSimulationAbsorbance(Absorbance):
  """Absorbance on a simulated reader: the read is checked and encoded, and every well reads a
  random optical density within the reader's range, or its value in `simulated_data`."""

  @staticmethod
  def _get_measured_values(
    plate: Plate, wells: List[Well], values: Dict[str, float], what: str
  ) -> Dict[str, float]:
    """The values of the measured wells in `values`, keyed by well identifier, row by row.

    Raises:
      ValueError: if `values` has no value for a measured well.
    """
    identifiers = _get_measured_identifiers(plate, wells)
    missing = [identifier for identifier in identifiers if identifier not in values]
    if missing:
      raise ValueError(f"{what} has no value for {missing}")
    return {identifier: float(values[identifier]) for identifier in identifiers}

  async def measure(
    self,
    *,
    wells: Optional[List[Well]] = None,
    wavelengths: Sequence[int],
    measurement_settings: Optional[MeasurementSettings] = None,
    shake_settings: Optional[ShakeSettings] = None,
    report: Report = "optical_density",
    timeout: Optional[float] = None,
    simulated_data: Optional[List[AbsorbanceResult]] = None,
  ) -> List[AbsorbanceResult]:
    """Check and encode the measurement; every measured well reads a random optical density.

    The temperature is the temperature controller's target, as the simulated status reports it.

    Args:
      wells, wavelengths, measurement_settings, shake_settings, report, timeout: as
        `Absorbance.measure`.
      simulated_data: results an earlier `measure` returned with the same report and
        wavelengths, on a reader or in simulation; each measured well reads its values there
        instead of random ones.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if an argument is out of range, the settings cannot read the wells of the plate,
        or `simulated_data` does not
        hold the same report and wavelengths, in the same order, with a value for every measured
        well.
      NotImplementedError: for raw counts without `simulated_data`: counts need the reader's
        calibration.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    plate, wells, _ = self._get_run_parameters(
      wavelengths, report, measurement, shake_settings, wells, timeout
    )
    wavelengths = list(wavelengths)
    logger.info(
      "absorbance: %s nm, %d of %d wells of %s, %s scan, %s",
      ", ".join(str(wavelength) for wavelength in wavelengths),
      len(wells),
      plate.num_items,
      plate.name,
      measurement.well_scan,
      report,
    )
    temperature = (await self._driver.request_status()).temperature_bottom
    timestamp = datetime.now()
    results: List[AbsorbanceResult] = []
    if simulated_data is None:
      if report == "raw":
        raise NotImplementedError("raw counts are not simulated; give simulated_data")
      measured = len({id(well) for well in wells})
      logger.debug("%s read: simulation: random optical densities", SIMULATED_LINK)
      for wavelength in wavelengths:
        densities = [
          round(random.uniform(*_OPTICAL_DENSITY_RANGE), _OPTICAL_DENSITY_RESOLUTION)
          for _ in range(measured)
        ]
        results.append(
          AbsorbanceResult(
            data=_get_readings_by_well(plate, wells, densities),
            wavelength=wavelength,
            temperature=temperature,
            start_timestamp=timestamp,
            end_timestamp=timestamp,
          )
        )
      return results
    raw = [isinstance(result, CLARIOstarAbsorbanceResult) for result in simulated_data]
    given = [result.wavelength for result in simulated_data]
    if given != wavelengths or any(is_raw != (report == "raw") for is_raw in raw):
      raise ValueError(f"simulated_data holds {report} at {given}, measuring {wavelengths}")
    logger.debug("%s read: simulation: simulated_data", SIMULATED_LINK)
    for result in simulated_data:
      what = f"simulated_data at {result.wavelength} nm"
      data = self._get_measured_values(plate, wells, result.data, what)
      if isinstance(result, CLARIOstarAbsorbanceResult):
        if result.references is None:
          raise ValueError(f"{what} is raw but holds no references")
        references = self._get_measured_values(
          plate, wells, result.references, what + " references"
        )
        results.append(
          CLARIOstarAbsorbanceResult(
            data=data,
            wavelength=result.wavelength,
            temperature=temperature,
            start_timestamp=timestamp,
            end_timestamp=timestamp,
            references=references,
            calibration=result.calibration,
            reference_calibration=result.reference_calibration,
          )
        )
      else:
        results.append(
          AbsorbanceResult(
            data=data,
            wavelength=result.wavelength,
            temperature=temperature,
            start_timestamp=timestamp,
            end_timestamp=timestamp,
          )
        )
    return results

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
    simulated_data: Optional[AbsorbanceSpectrumResult] = None,
  ) -> AbsorbanceSpectrumResult:
    """Check and encode the spectrum; every measured well reads a random optical density at
    every wavelength.

    Args:
      wells, start_wavelength, end_wavelength, step_size, measurement_settings, shake_settings,
        report, timeout: as `Absorbance.measure_spectrum`.
      simulated_data: a spectrum an earlier `measure_spectrum` returned with the same report, on a
        reader or in simulation; each measured well reads its spectrum there instead of random
        values.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if an argument is out of range, the settings cannot read the wells of the plate,
        or `simulated_data` is not the same report with every measured well at every wavelength.
      NotImplementedError: for raw counts without `simulated_data`: counts need the reader's
        calibration.
    """
    measurement = MeasurementSettings() if measurement_settings is None else measurement_settings
    plate, wells, _ = self._get_spectrum_run_parameters(
      start_wavelength,
      end_wavelength,
      step_size,
      report,
      measurement,
      shake_settings,
      wells,
      timeout,
    )
    wavelengths = _get_spectrum_wavelengths(start_wavelength, end_wavelength, step_size)
    identifiers = _get_measured_identifiers(plate, wells)
    logger.info(
      "absorbance spectrum: %d to %d nm in %d nm steps, %d of %d wells of %s, %s scan, %s",
      start_wavelength,
      end_wavelength,
      step_size,
      len(identifiers),
      plate.num_items,
      plate.name,
      measurement.well_scan,
      report,
    )
    temperature = (await self._driver.request_status()).temperature_bottom
    timestamp = datetime.now()
    if simulated_data is None:
      if report == "raw":
        raise NotImplementedError("raw counts are not simulated; give simulated_data")
      logger.debug("%s read: simulation: random optical densities", SIMULATED_LINK)
      data = {
        identifier: {
          wavelength: round(random.uniform(*_OPTICAL_DENSITY_RANGE), _OPTICAL_DENSITY_RESOLUTION)
          for wavelength in wavelengths
        }
        for identifier in identifiers
      }
      return AbsorbanceSpectrumResult(
        data=data, temperature=temperature, start_timestamp=timestamp, end_timestamp=timestamp
      )
    raw = isinstance(simulated_data, CLARIOstarAbsorbanceSpectrumResult)
    if raw != (report == "raw"):
      raise ValueError(f"simulated_data is not a {report} spectrum")
    missing = [
      identifier
      for identifier in identifiers
      if any(
        wavelength not in simulated_data.data.get(identifier, {}) for wavelength in wavelengths
      )
    ]
    if missing:
      raise ValueError(f"simulated_data has no full spectrum for {missing}")
    logger.debug("%s read: simulation: simulated_data", SIMULATED_LINK)
    data = {
      identifier: {
        wavelength: float(simulated_data.data[identifier][wavelength]) for wavelength in wavelengths
      }
      for identifier in identifiers
    }
    if isinstance(simulated_data, CLARIOstarAbsorbanceSpectrumResult):
      if simulated_data.references is None or simulated_data.calibration is None:
        raise ValueError("simulated_data is raw but holds no references or calibration")
      references = self._get_measured_values(
        plate, wells, simulated_data.references, "simulated_data references"
      )
      return CLARIOstarAbsorbanceSpectrumResult(
        data=data,
        temperature=temperature,
        start_timestamp=timestamp,
        end_timestamp=timestamp,
        references=references,
        calibration={
          wavelength: simulated_data.calibration[wavelength] for wavelength in wavelengths
        },
        reference_calibration=simulated_data.reference_calibration,
      )
    return AbsorbanceSpectrumResult(
      data=data, temperature=temperature, start_timestamp=timestamp, end_timestamp=timestamp
    )


class CLARIOstarSimulationDriver(CLARIOstarDriver):
  """A CLARIOstar driver with no reader behind it.

  Nothing reaches a wire. The configuration comes from ``declared_configuration_json``, and the
  convenience requests answer from it; status comes from the simulated state and the resource
  model; the drawer moves at once. Reading the raw stored parameters is not simulated.
  """

  def __init__(self, declared_configuration_json: str, name: str = "CLARIOstar"):
    """
    Args:
      declared_configuration_json: a file `save_configuration` wrote; the simulated reader's
        configuration.
      name: the device name, at the start of every log line.
    """
    super().__init__(name=name, declared_configuration_json=declared_configuration_json)
    self.initialized = False
    self._simulation_drawer = CLARIOstarSimulationDrawer(self)
    self.drawer = self._simulation_drawer

  def _build_io(self, device_id: Optional[str]) -> Union[FTDI, USB]:
    """Build no link."""
    return None  # type: ignore[return-value]  # no link: nothing reaches a wire

  async def exchange(self, payload: bytes) -> bytes:
    """Raise: a simulated reader sends nothing."""
    raise NotImplementedError("a simulated reader sends nothing")

  async def send_command(self, *args, **kwargs) -> bytes:  # type: ignore[override]
    """Raise: a simulated reader sends nothing."""
    raise NotImplementedError("a simulated reader sends nothing")

  async def request_status(self, retries: int = 5) -> CLARIOstarStatus:
    """The simulated state: never busy, the drawer and plate where the resource model has them,
    motors on once initialized, the temperature controller's target as both temperatures."""
    temperature = (
      self.temperature_controller.target_temperature
      if self.temperature_controller is not None
      else None
    )
    return CLARIOstarStatus(
      busy=False,
      plate_detected=self._simulation_drawer._get_plate_present(),
      drawer_open=self._simulation_drawer._get_open(),
      motors_enabled=self.initialized,
      temperature_bottom=temperature,
      temperature_top=temperature,
    )

  async def _wait_until_ready(
    self,
    timeout: Optional[float],
    poll_interval: float,
    until: Optional[Callable[[CLARIOstarStatus], bool]] = None,
  ) -> None:
    """Check the condition once against the simulated state; the reader is never busy."""
    status = await self.request_status()
    if until is not None and not until(status):
      raise TimeoutError(f"simulated CLARIOstar status {status} does not meet the condition")

  async def initialize(self) -> None:
    """Mark the reader initialized, motors on."""
    self.initialized = True

  async def request_parameters(self) -> Dict[str, int]:
    """Raise: the raw stored parameters are not simulated."""
    raise NotImplementedError("the stored parameters are not simulated; read `configuration`")

  async def request_fluorescence_channels(self) -> int:
    """The number of fluorescence channels, from the configuration."""
    return _require_read(self.configuration.fluorescence_channels, "fluorescence channels")

  async def request_luminescence_channels(self) -> int:
    """The number of luminescence channels, from the configuration."""
    return _require_read(self.configuration.luminescence_channels, "luminescence channels")

  async def request_time_resolved_fluorescence_channels(self) -> int:
    """The number of time-resolved fluorescence channels, from the configuration."""
    channels = self.configuration.time_resolved_fluorescence_channels
    return _require_read(channels, "time-resolved fluorescence channels")

  async def request_absorbance_available(self) -> bool:
    """Whether absorbance is available, from the configuration."""
    return _require_read(self.configuration.absorbance_available, "absorbance")

  async def request_alpha_screen_available(self) -> bool:
    """Whether AlphaScreen is available, from the configuration."""
    return _require_read(self.configuration.alpha_screen_available, "AlphaScreen")

  async def request_fluorescence_polarization_available(self) -> bool:
    """Whether fluorescence polarization is available, from the configuration."""
    available = self.configuration.fluorescence_polarization_available
    return _require_read(available, "fluorescence polarization")

  async def request_bottom_measurement_available(self) -> bool:
    """Whether bottom measurement is available, from the configuration."""
    return _require_read(self.configuration.bottom_measurement_available, "bottom measurement")

  async def request_extended_temperature_range(self) -> bool:
    """Whether the extended temperature range is built in, from the configuration."""
    return _require_read(
      self.configuration.extended_temperature_range, "extended temperature_controller"
    )

  async def request_high_speed_shaking_allowed(self) -> bool:
    """Whether high speed shaking is allowed, from the configuration."""
    return _require_read(self.configuration.high_speed_shaking_allowed, "high speed shaking")

  async def request_well_1536_plates_supported(self) -> bool:
    """Whether the reader is set up for 1536-well plates, from the configuration."""
    return _require_read(self.configuration.well_1536_plates_supported, "1536-well plates")

  async def discover(self) -> None:
    """Read the configuration from ``declared_configuration_json``."""
    if self.declared_configuration_json is None:
      raise RuntimeError("a simulated reader needs a declared configuration")
    fields = read_configuration(self.declared_configuration_json)["device"]
    self.configuration = configuration_from_json(fields)
    self._check_firmware_compatibility()
    self._discover_features()

  def _discover_features(self) -> None:
    """Build the simulated features the configuration says are fitted."""
    if self.configuration.temperature_controller and self.temperature_controller is None:
      self.temperature_controller = CLARIOstarSimulationTemperatureController(self)
    if self.configuration.absorbance_available and self.absorbance is None:
      self.absorbance = CLARIOstarSimulationAbsorbance(self)

  def _describe_link(self) -> str:
    """The simulated link, and the configuration it answers from."""
    configuration = os.path.basename(self.declared_configuration_json or "no configuration")
    return f"{SIMULATED_LINK} ({configuration})"

  async def setup(self) -> None:
    """Initialize and read the configuration, with nothing to connect."""
    logger.debug("setting up the CLARIOstar on %s ...", self._describe_link())
    logger.debug("[PHASE 1] Device initialization")
    await self.initialize()
    logger.debug("[PHASE 2] Discovery")
    await self.discover()
    logger.debug("[PHASE 3] Temperature monitoring")
    await self._start_temperature_monitoring()
    logger.info("%s", self.format_setup_summary())

  async def _stop_run(self) -> None:
    """Nothing to stop: a simulated read ends before `measure` returns."""

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Shut down as a reader does, then mark it not initialized; there is nothing to close.

    Raises:
      RuntimeError: if a plate is still in the reader, unless `accept_plate_left_in_device`.
    """
    await self._shut_down(accept_plate_left_in_device)
    self.initialized = False
