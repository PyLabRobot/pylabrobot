"""BMG Labtech VANTAstar plate reader as a PyLabRobot resource."""

import logging
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from pylabrobot.io.usb import USB
from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.lib.fluorescence.results import FluorescenceResult
from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Resource, Well

from .driver.master import (
  DEFAULT_APERTURE_DISTANCE,
  LUMINESCENCE_NO_FILTER,
  MonochromatorSettings,
  PlateGeometry,
  ShakeMode,
  VANTAstarDriver,
  compute_monochromator_settings,
  encode_well_mask,
)
from .driver.responses import GainFocus, MeasurementData, compute_optical_densities

logger = logging.getLogger(__name__)

SIZE_X = 420.0
SIZE_Y = 520.0
SIZE_Z = 295.0

# Monochromator limits of BMG's filter software, in nm: the bandwidths it allows, the shortest
# excitation wavelength and lower excitation edge, and the longest wavelength (with the extended
# infrared detector; without it Voyager stops at 740 nm).
MINIMUM_BANDWIDTH = 8.0
MAXIMUM_BANDWIDTH = 100.0
MINIMUM_EXCITATION = 320.0
MINIMUM_EXCITATION_EDGE = 310.0
MAXIMUM_WAVELENGTH = 840.0


class _VANTAstarPlateHolder(PlateHolder):
  """The drawer's plate holder: a plate goes on or comes off only while the drawer is open."""

  def __init__(self, name: str):
    """
    Args:
      name: the resource name.
    """
    super().__init__(
      name=name,
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),
      model="vantastar_plate_holder",
    )
    self._drawer_open: Optional[bool] = None

  def _require_drawer_open(self, action: str) -> None:
    """Raise unless the drawer was out when last moved.

    Raises:
      RuntimeError: if the drawer has not been moved yet, or was moved in.
    """
    if self._drawer_open is None:
      raise RuntimeError(f"cannot {action}: the drawer has not been sensed; open it first")
    if not self._drawer_open:
      raise RuntimeError(f"cannot {action}: the drawer is closed; open it first")

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Raise unless the drawer is open and the holder can take ``resource``."""
    self._require_drawer_open(f"place {resource.name} on {self.name}")
    super().check_can_drop_resource_here(resource, reassign=reassign)

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate] = None,
    reassign: bool = True,
  ):
    """Place ``resource``, checked as a drop is (``check_can_drop_resource_here``)."""
    self.check_can_drop_resource_here(resource, reassign=reassign)
    return super().assign_child_resource(resource, location, reassign)

  def unassign_child_resource(self, resource: Resource):
    """Take ``resource`` off; only while the drawer is open."""
    self._require_drawer_open(f"remove {resource.name} from {self.name}")
    return super().unassign_child_resource(resource)


def get_plate_geometry(plate: Plate) -> PlateGeometry:
  """The reader's view of ``plate``: well centres from the plate's left and back edges.

  Raises:
    ValueError: if the plate's wells have no location.
  """
  items = plate.get_all_items()
  first, last = items[0], items[-1]
  if first.location is None or last.location is None:
    raise ValueError(f"the wells of {plate.name} have no location")
  first_center = first.location + first.center()
  last_center = last.location + last.center()
  return PlateGeometry(
    length=plate.get_size_x(),
    width=plate.get_size_y(),
    first_x=first_center.x,
    first_y=plate.get_size_y() - first_center.y,
    last_x=last_center.x,
    last_y=plate.get_size_y() - last_center.y,
    columns=plate.num_items_x,
    rows=plate.num_items_y,
    well_size=min(first.get_size_x(), first.get_size_y()),
  )


def _get_measured_wells(plate: Plate, wells: Optional[Sequence[Well]]) -> List[Tuple[int, int]]:
  """The (row, column) of each measured well, in the reader's data order: row by row.

  Raises:
    ValueError: if a well is not on ``plate``.
  """
  rows = plate.num_items_y
  index = {id(w): i for i, w in enumerate(plate.get_all_items())}  # items are column by column
  if wells is None:
    positions = [(i % rows, i // rows) for i in range(len(index))]
  else:
    unknown = [w.name for w in wells if id(w) not in index]
    if unknown:
      raise ValueError(f"{plate.name} has no wells {unknown}")
    positions = [(index[id(w)] % rows, index[id(w)] // rows) for w in wells]
  return sorted(set(positions))


def _get_identifier(row: int, column: int) -> str:
  """The well identifier of a 0-based row and column, e.g. ``"B3"``."""
  return f"{chr(ord('A') + row)}{column + 1}"


class VANTAstarDevice(Resource):
  """The VANTAstar as a PyLabRobot resource: its plate holder and the driver that runs it.

  Build one with the ``VANTAstar`` factory.
  """

  def __init__(
    self,
    name: str = "VANTAstar",
    *,
    device_id: Optional[str] = None,
    io: Optional[USB] = None,
    size_x: float = SIZE_X,
    size_y: float = SIZE_Y,
    size_z: float = SIZE_Z,
  ):
    """
    Args:
      name: the resource name.
      device_id: the USB serial number; None uses the first reader found.
      io: an already-built ``USB``, used instead of building one.
      size_x: width, in mm.
      size_y: depth, in mm.
      size_z: height, in mm.
    """
    super().__init__(
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category="device",
      model="VANTAstar",
    )
    self.driver = VANTAstarDriver(device_id=device_id, io=io, name=name)
    self.plate_holder = _VANTAstarPlateHolder(f"{name}_plate_holder")
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def check_can_drop_resource_here(self, resource: Resource, *, reassign: bool = True) -> None:
    """Raise: a plate goes on the plate holder, not on the reader."""
    raise RuntimeError(
      f"cannot place {resource.name} on {self.name}: place it on {self.plate_holder.name}"
    )

  def assign_child_resource(
    self,
    resource: Resource,
    location: Optional[Coordinate],
    reassign: bool = True,
  ):
    """Assign the reader's own plate holder; anything else goes on the plate holder."""
    if resource is not self.plate_holder:
      self.check_can_drop_resource_here(resource, reassign=reassign)
    return super().assign_child_resource(resource, location, reassign)

  async def setup(self) -> None:
    """Connect and initialize the reader (about 10 s)."""
    await self.driver.setup()

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Stop a running measurement, close the drawer, and disconnect.

    Raises:
      RuntimeError: if a plate is still in the reader, unless ``accept_plate_left_in_device``.
    """
    await self.driver.stop(accept_plate_left_in_device=accept_plate_left_in_device)

  async def open_drawer(self) -> None:
    """Move the drawer out (about 7 s)."""
    await self.driver.open_drawer()
    self.plate_holder._drawer_open = True

  async def close_drawer(self) -> None:
    """Move the drawer in (about 10 s)."""
    await self.driver.close_drawer()
    self.plate_holder._drawer_open = False

  def _get_plate(self, plate: Optional[Plate]) -> Plate:
    """``plate``, or the plate on the plate holder.

    Raises:
      RuntimeError: if ``plate`` is None and no plate is on the plate holder.
    """
    if plate is not None:
      return plate
    if not isinstance(self.plate_holder.resource, Plate):
      raise RuntimeError(f"no plate on {self.plate_holder.name}")
    return self.plate_holder.resource

  @staticmethod
  def _get_well_mask(plate: Plate, wells: Optional[Sequence[Well]]) -> Optional[bytes]:
    """The well mask of ``wells`` on ``plate``; None for every well."""
    if wells is None:
      return None
    return encode_well_mask(plate.num_items_x, plate.num_items_y, _get_measured_wells(plate, wells))

  @staticmethod
  def _check_settling_time(settling_time: float) -> None:
    """Raise unless the settling time fits the reader's byte of 20 ms steps."""
    if not 0 <= settling_time <= 5.1:
      raise ValueError(f"settling_time must be 0-5.1 s, got {settling_time}")

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

  @staticmethod
  def _check_wells(data: MeasurementData, measured: List[Tuple[int, int]]) -> None:
    """Raise unless the reader measured as many wells as were asked for."""
    if data.wells != len(measured):
      raise RuntimeError(f"the reader measured {data.wells} wells, {len(measured)} were asked for")

  async def measure_absorbance(
    self,
    wavelengths: Sequence[int],
    *,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    flashes: int = 22,
    settling_time: float = 0.5,
    timeout: float = 300.0,
  ) -> List[AbsorbanceResult]:
    """Measure the absorbance of the plate at each wavelength (endpoint, point read).

    The defaults are Voyager's "precise" preset.  The optical density is computed as Voyager
    computes it, against the reader's 100% and reference values; it is not pathlength-corrected.

    Args:
      wavelengths: 1 to 8 wavelengths in nm (220-1000).
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      flashes: flashes per well (1-200).
      settling_time: seconds to settle before reading each well (0-5.1, 20 ms steps).
      timeout: seconds the run may take.

    Returns:
      One ``AbsorbanceResult`` per wavelength, in the order given.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    if not 1 <= len(wavelengths) <= 8:
      raise ValueError(f"1 to 8 wavelengths, got {len(wavelengths)}")
    for wavelength in wavelengths:
      if not 220 <= wavelength <= 1000:
        raise ValueError(f"wavelength must be 220-1000 nm, got {wavelength}")
    if not 1 <= flashes <= 200:
      raise ValueError(f"flashes must be 1-200, got {flashes}")
    self._check_settling_time(settling_time)
    plate = self._get_plate(plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self.driver.build_absorbance_run(
      wavelengths,
      flashes=flashes,
      settling_time=settling_time,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
    )
    start = datetime.now()
    data = await self.driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    self._check_wells(data, measured)
    identifiers = [_get_identifier(r, c) for r, c in measured]
    results = []
    for i, wavelength in enumerate(wavelengths):
      densities = compute_optical_densities(data, i)
      results.append(
        AbsorbanceResult(
          data=dict(zip(identifiers, densities)),
          wavelength=wavelength,
          temperature=data.temperature,
          start_timestamp=start,
          end_timestamp=end,
        )
      )
    return results

  async def measure_fluorescence(
    self,
    excitation_wavelength: float,
    emission_wavelength: float,
    *,
    excitation_bandwidth: float,
    emission_bandwidth: float,
    focal_height: float,
    gain: Optional[int] = None,
    dichroic_split_wavelength: Optional[float] = None,
    monochromator: Optional[MonochromatorSettings] = None,
    enhanced_dynamic_range: bool = False,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    flashes: int = 20,
    settling_time: float = 0.5,
    timeout: float = 300.0,
  ) -> FluorescenceResult:
    """Measure the fluorescence intensity of the plate from the top (endpoint, point read).

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
      focal_height: mm, in 0.1 mm steps (``adjust_gain_and_focus`` finds one).
      gain: the detector gain; required unless ``enhanced_dynamic_range``.
      dichroic_split_wavelength: nm; None sets it as Voyager does automatically, midway between the
        excitation's upper and the emission's lower edge.
      monochromator: band edges, dichroic and slits to read with instead of the computed ones.
      enhanced_dynamic_range: read with enhanced dynamic range; no gain is sent.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      flashes: flashes per well (1-200).
      settling_time: seconds to settle before reading each well (0-5.1, 20 ms steps).
      timeout: seconds the run may take.

    Returns:
      The counts of the measured wells.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    self._check_optics(
      excitation_wavelength, excitation_bandwidth, emission_wavelength, emission_bandwidth
    )
    if gain is None and not enhanced_dynamic_range:
      raise ValueError("a gain is required unless enhanced_dynamic_range")
    if not 1 <= flashes <= 200:
      raise ValueError(f"flashes must be 1-200, got {flashes}")
    self._check_settling_time(settling_time)
    if monochromator is None:
      monochromator = compute_monochromator_settings(
        excitation_wavelength,
        excitation_bandwidth,
        emission_wavelength,
        emission_bandwidth,
        dichroic_split_wavelength,
      )
    plate = self._get_plate(plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self.driver.build_fluorescence_run(
      [monochromator],
      gains=[gain or 0],
      focal_height=focal_height,
      flashes=flashes,
      settling_time=settling_time,
      enhanced_dynamic_range=enhanced_dynamic_range,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
    )
    start = datetime.now()
    data = await self.driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
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
      excitation_wavelength=excitation_wavelength,
      excitation_bandwidth=excitation_bandwidth,
      emission_wavelength=emission_wavelength,
      emission_bandwidth=emission_bandwidth,
      temperature=data.temperature,
      start_timestamp=start,
      end_timestamp=end,
    )

  async def measure_luminescence(
    self,
    *,
    integration_time: float = 0.8,
    gain: Optional[int] = None,
    aperture_distance: float = DEFAULT_APERTURE_DISTANCE,
    monochromator: MonochromatorSettings = LUMINESCENCE_NO_FILTER,
    plate: Optional[Plate] = None,
    wells: Optional[Sequence[Well]] = None,
    settling_time: float = 0.02,
    timeout: float = 600.0,
  ) -> LuminescenceResult:
    """Measure the luminescence of the plate from the top (endpoint).  NOT TESTED on a reader.

    The defaults are those of Voyager's luminescence protocol: all the emitted light, automatic
    gain, 0.8 s per well, 0.5 mm aperture distance, 20 ms settling.  Neither the command nor the
    decoding of its data has run on a reader with luminescence.  Please report how it behaves.

    Args:
      integration_time: seconds of light collection per well (0.01-655.35, 10 ms steps).
      gain: the detector gain; None for the reader's automatic gain.
      aperture_distance: mm between the top optic's aperture and the plate (0.01 mm steps).
      monochromator: the emission settings; the default reads all the emitted light, as
        Voyager's luminescence protocol does.
      plate: the plate to measure; None uses the plate on the plate holder.
      wells: the wells to measure; None measures every well.
      settling_time: seconds to settle before reading each well (0-5.1, 20 ms steps).
      timeout: seconds the run may take.

    Returns:
      The counts of the measured wells.

    Raises:
      RuntimeError: if no plate is on the plate holder or the run ends without its data.
      ValueError: if an argument is out of range.
    """
    logger.warning("luminescence has not been tested on a VANTAstar; please report how it behaves")
    if not 0.01 <= integration_time <= 655.35:
      raise ValueError(f"integration_time must be 0.01-655.35 s, got {integration_time}")
    self._check_settling_time(settling_time)
    plate = self._get_plate(plate)
    measured = _get_measured_wells(plate, wells)
    parameters = self.driver.build_luminescence_run(
      [monochromator],
      gains=None if gain is None else [gain],
      integration_time=integration_time,
      settling_time=settling_time,
      aperture_distance=aperture_distance,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
    )
    start = datetime.now()
    data = await self.driver.run_measurement(parameters, timeout=timeout)
    end = datetime.now()
    self._check_wells(data, measured)
    return LuminescenceResult(
      data={_get_identifier(r, c): float(v) for (r, c), v in zip(measured, data.group(0))},
      emission_wavelength=None,
      emission_bandwidth=None,
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
    target_percent: float = 88.0,
    dichroic_split_wavelength: Optional[float] = None,
    monochromator: Optional[MonochromatorSettings] = None,
    plate: Optional[Plate] = None,
    timeout: float = 300.0,
  ) -> GainFocus:
    """Find the gain, and the focal height, for a top fluorescence read on reference wells.

    The reader scans the focal height from 16 mm down in 0.2 mm steps and sets the gain so the
    brightest reference well reads at ``target_percent`` of the detector range, as Voyager's
    automatic gain and focus does.

    Args:
      excitation_wavelength: nm.
      emission_wavelength: nm.
      excitation_bandwidth: nm, 8-100.
      emission_bandwidth: nm, 8-100.
      wells: the reference wells.
      focal_height: mm; the gain is adjusted at this height when ``adjust_focus`` is False.
      adjust_focus: also find the focal height.
      target_percent: the gain's target, percent of the detector range.
      dichroic_split_wavelength: nm; None as in ``measure_fluorescence``.
      monochromator: band edges, dichroic and slits instead of the computed ones.
      plate: the plate; None uses the plate on the plate holder.
      timeout: seconds the adjustment may take.

    Returns:
      The gain and focal height found; pass them to ``measure_fluorescence``.

    Raises:
      RuntimeError: if no plate is on the plate holder.
      ValueError: if the optics are out of the limits (`_check_optics`).
    """
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
    parameters = self.driver.build_gain_focus(
      monochromator,
      focal_height=focal_height,
      target_percent=target_percent,
      adjust_focus=adjust_focus,
      geometry=get_plate_geometry(plate),
      well_mask=self._get_well_mask(plate, wells),
    )
    return await self.driver.adjust_gain_and_focus(parameters, timeout=timeout)

  async def shake(self, mode: ShakeMode, speed: int, duration: int) -> None:
    """Shake the plate.

    Args:
      mode: "orbital", "double_orbital" or "linear".
      speed: rpm, a multiple of 100.
      duration: seconds.
    """
    await self.driver.shake(mode, speed, duration)
    self.plate_holder._drawer_open = False

  async def set_temperature(self, temperature: float) -> None:
    """Heat the incubator to ``temperature`` degrees C."""
    await self.driver.set_temperature(temperature)

  async def stop_temperature_control(self) -> None:
    """Switch heating off; the temperature stays monitored."""
    await self.driver.stop_temperature_control()

  async def get_temperature(self) -> Optional[float]:
    """The incubator temperature Voyager displays, in degrees C, or None if the reader reports
    none."""
    temperatures = await self.driver.get_temperatures()
    return None if temperatures is None else temperatures[0]


def VANTAstar(
  name: str = "VANTAstar",
  *,
  device_id: Optional[str] = None,
  io: Optional[USB] = None,
  size_x: float = SIZE_X,
  size_y: float = SIZE_Y,
  size_z: float = SIZE_Z,
) -> VANTAstarDevice:
  """Build a VANTAstar plate reader resource.

  Args:
    name: the resource name.
    device_id: the USB serial number; None uses the first reader found.
    io: an already-built ``USB``; used instead of building one.
    size_x: width, in mm.
    size_y: depth, in mm.
    size_z: height, in mm.
  """
  return VANTAstarDevice(
    name=name,
    device_id=device_id,
    io=io,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
  )
