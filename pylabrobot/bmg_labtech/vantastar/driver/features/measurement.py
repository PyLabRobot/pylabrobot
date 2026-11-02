"""What every measurement feature (RUN) shares: the RUN parameters and their encoding, the plate and
its wells."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, List, Literal, Optional, Sequence, Tuple

from pylabrobot.resources import CrossSectionType, Plate, Well

from ..responses import MeasurementData

if TYPE_CHECKING:
  from ..master import VANTAstarDriver

# Slits Voyager sets in a fluorescence scan: 2 on the scanned side, 3 on the fixed one.
_SCANNED_SLIT = 2
_FIXED_SLIT = 3
# Luminescence's aperture distance when none is given (Voyager's default, 0.5 mm).
DEFAULT_APERTURE_DISTANCE = 0.5

WellScanMode = Literal["orbital", "spiral"]
ScanSide = Literal["excitation", "emission"]

# WellScanningMode values (RUN and GainPlateCommand).
_WELL_SCAN_MODES = {"orbital": 3, "spiral": 4}


@dataclass(frozen=True)
class WellScan:
  """Averaging over an area of each well instead of reading its centre: the reader reads along an
  orbit, or a spiral, of ``diameter`` mm around the centre.  The reader refuses a diameter wider
  than the well (quit code 65; 7 mm on a 96-well plate with 6.96 mm wells)."""

  mode: WellScanMode
  diameter: int


@dataclass(frozen=True)
class PlateGeometry:
  """A plate as the reader sees it, in mm: its size, the centres of its first and last well (from
  the plate's left and back edges), its grid, and its well size (a round well's diameter, a square
  well's width) and shape (0 round, 1 square, as Voyager sends them)."""

  length: float
  width: float
  first_x: float
  first_y: float
  last_x: float
  last_y: float
  columns: int
  rows: int
  well_size: float
  well_shape: int = 0


# Voyager's "STANDARD 96" plate definition.
STANDARD_96 = PlateGeometry(
  length=127.76,
  width=85.48,
  first_x=14.38,
  first_y=11.24,
  last_x=113.38,
  last_y=74.24,
  columns=12,
  rows=8,
  well_size=6.58,
)


@dataclass(frozen=True)
class MonochromatorSettings:
  """The monochromator of one fluorescence chromatic: band edges and dichroic in nm, slit
  positions as the reader numbers them (excitation 3 = 1.0 mm, emission 4 = 2.0 mm, the slits
  BMG's filter software sets for fluorescence)."""

  excitation_lower: float
  excitation_upper: float
  dichroic: float
  emission_lower: float
  emission_upper: float
  excitation_slit: int = 3
  emission_slit: int = 4


# Voyager's luminescence optics with no filter, as BMG's filter software returns them: raw value 1
# for the dichroic and both emission edges, the LUM lens (6) in the emission slit, excitation
# unused.
LUMINESCENCE_NO_FILTER = MonochromatorSettings(
  excitation_lower=0,
  excitation_upper=0,
  dichroic=0.1,
  emission_lower=0.1,
  emission_upper=0.1,
  excitation_slit=0,
  emission_slit=6,
)


def compute_monochromator_settings(
  excitation_wavelength: float,
  excitation_bandwidth: float,
  emission_wavelength: float,
  emission_bandwidth: float,
  dichroic: Optional[float] = None,
) -> MonochromatorSettings:
  """Nominal band edges (centre +/- bandwidth / 2) and, by default, the dichroic Voyager sets
  automatically: midway between the excitation's upper and the emission's lower nominal edge, in
  0.1 nm (rounded half to even).

  Voyager computes the band edges with BMG's filter software, which narrows them symmetrically by
  an amount that depends on the wavelength, the bandwidth and the slit: for excitation 365/8 and
  emission 400/10 it sends 362.9-367.1 and 397.8-402.2 nm, where the nominal edges are 361-369 and
  395-405 nm.  Pass ``MonochromatorSettings`` directly to read with Voyager's edges.
  """
  excitation_upper = excitation_wavelength + excitation_bandwidth / 2
  emission_lower = emission_wavelength - emission_bandwidth / 2
  return MonochromatorSettings(
    excitation_lower=excitation_wavelength - excitation_bandwidth / 2,
    excitation_upper=excitation_upper,
    dichroic=(excitation_upper + emission_lower) / 2 if dichroic is None else dichroic,
    emission_lower=emission_lower,
    emission_upper=emission_wavelength + emission_bandwidth / 2,
  )


def compute_scan_monochromators(
  scan: ScanSide,
  start_wavelength: float,
  end_wavelength: float,
  fixed_wavelength: float,
  *,
  excitation_bandwidth: float,
  emission_bandwidth: float,
  dichroic: Optional[float] = None,
) -> Tuple[MonochromatorSettings, MonochromatorSettings]:
  """The monochromator settings of a fluorescence scan's first and last point: nominal band edges,
  the dichroic as ``compute_monochromator_settings`` sets it for each point unless given, and the
  slits Voyager sets in a scan (2 on the scanned side, 3 on the fixed one).

  Args:
    scan: the scanned side; the other stays at ``fixed_wavelength``.
    start_wavelength: the first point's wavelength in nm.
    end_wavelength: the last point's wavelength in nm.
    fixed_wavelength: nm.
    excitation_bandwidth: nm.
    emission_bandwidth: nm.
    dichroic: nm for every point; None to compute it per point.
  """
  points = []
  for wavelength in (start_wavelength, end_wavelength):
    excitation, emission = (
      (wavelength, fixed_wavelength) if scan == "excitation" else (fixed_wavelength, wavelength)
    )
    settings = compute_monochromator_settings(
      excitation, excitation_bandwidth, emission, emission_bandwidth, dichroic
    )
    points.append(
      replace(
        settings,
        excitation_slit=_SCANNED_SLIT if scan == "excitation" else _FIXED_SLIT,
        emission_slit=_SCANNED_SLIT if scan == "emission" else _FIXED_SLIT,
      )
    )
  return points[0], points[1]


def encode_well_scan(well_scan: Optional[WellScan]) -> bytes:
  """The RUN well scanning fields: the mode, then for a scan the matrix width (0) and the diameter
  in mm; a point read is mode 0 alone.

  Raises:
    ValueError: if the mode is unknown or the diameter not 1-255 mm.
  """
  if well_scan is None:
    return b"\x00"
  if well_scan.mode not in _WELL_SCAN_MODES:
    raise ValueError(f"well scan mode must be one of {sorted(_WELL_SCAN_MODES)}")
  if not 1 <= well_scan.diameter <= 0xFF:
    raise ValueError(f"well scan diameter must be 1-255 mm, got {well_scan.diameter}")
  return bytes([_WELL_SCAN_MODES[well_scan.mode], 0, well_scan.diameter])


def _tenth_nm(wavelength: float) -> bytes:
  """A wavelength in nm as u16 BE in 0.1 nm."""
  return round(wavelength * 10).to_bytes(2, "big")


def _hundredth_mm(length: float) -> bytes:
  """A length in mm as u16 BE in 0.01 mm."""
  return round(length * 100).to_bytes(2, "big")


def _focal_height_bytes(focal_height: float) -> bytes:
  """A focal height in mm as the reader takes it: 0.01 mm units, in Voyager's 0.1 mm steps."""
  return (round(focal_height * 10) * 10).to_bytes(2, "big")


def encode_monochromator(settings: MonochromatorSettings) -> bytes:
  """The 14-byte monochromator block: excitation short-pass (upper edge) and long-pass (lower edge),
  dichroic, emission short-pass and long-pass, in 0.1 nm, then the emission and excitation slit."""
  return (
    _tenth_nm(settings.excitation_upper)
    + _tenth_nm(settings.excitation_lower)
    + _tenth_nm(settings.dichroic)
    + _tenth_nm(settings.emission_upper)
    + _tenth_nm(settings.emission_lower)
    + settings.emission_slit.to_bytes(2, "big")
    + settings.excitation_slit.to_bytes(2, "big")
  )


def encode_plate_geometry(geometry: PlateGeometry) -> bytes:
  """The 17-byte plate block: lengths in 0.01 mm, columns, rows, well size, well shape."""
  return (
    b"".join(
      _hundredth_mm(v)
      for v in (
        geometry.length,
        geometry.width,
        geometry.first_x,
        geometry.first_y,
        geometry.last_x,
        geometry.last_y,
      )
    )
    + bytes([geometry.columns, geometry.rows])
    + _hundredth_mm(geometry.well_size)
    + bytes([geometry.well_shape])
  )


def encode_well_mask(
  columns: int, rows: int, wells: Optional[Sequence[Tuple[int, int]]] = None
) -> bytes:
  """The 48-byte well mask.  ``wells`` are 0-based (row, column) pairs; None selects all wells.
  Bit = row * columns + column, most significant bit first.

  Raises:
    ValueError: if the plate has more than 384 wells.
  """
  if columns * rows > 8 * 48:
    raise ValueError(f"the well mask holds up to 384 wells, the plate has {columns * rows}")
  selected = None if wells is None else set(wells)
  mask = bytearray(48)
  for r in range(rows):
    for c in range(columns):
      if selected is None or (r, c) in selected:
        bit = r * columns + c
        mask[bit // 8] |= 0x80 >> (bit % 8)
  return bytes(mask)


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
    well_shape=1 if first.cross_section_type == CrossSectionType.RECTANGLE else 0,
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


class Measurement:
  """What every measurement feature shares: the plate, the wells and the checks of their settings.

  A feature builds its RUN fields with the driver's builders and runs them through the driver.
  """

  def __init__(self, driver: "VANTAstarDriver"):
    """
    Args:
      driver: the driver to send commands through.
    """
    self._driver = driver

  def _get_plate(self, plate: Optional[Plate]) -> Plate:
    """``plate``, or the plate on the plate holder.

    Raises:
      RuntimeError: if ``plate`` is None and no plate is on the plate holder.
    """
    if plate is not None:
      return plate
    plate_holder = self._driver.drawer.plate_holder
    if plate_holder is None:
      raise RuntimeError("the drawer has no plate holder; build the reader as a VANTAstarDevice")
    if not isinstance(plate_holder.resource, Plate):
      raise RuntimeError(f"no plate on {plate_holder.name}")
    return plate_holder.resource

  @staticmethod
  def _compute_default_focal_height(wells: Sequence[Well], percent: float) -> float:
    """The focal height ``percent`` of the depth up ``wells``, mm above the plate's base, as the
    CLARIOstar computes it: the well's location, its floor and the percent of its depth, rounded
    to 0.1 mm.

    Raises:
      ValueError: if a well has no location or floor thickness, or the wells differ in that height.
    """
    heights = set()
    for well in wells:
      if well.location is None:
        raise ValueError(f"well {well.name} has no location on its plate")
      try:
        floor = well.material_z_thickness
      except NotImplementedError as error:
        raise ValueError(f"well {well.name} has no floor thickness; give focal_height") from error
      heights.add(round(well.location.z + floor + well.get_size_z() * percent / 100, 1))
    if len(heights) > 1:
      raise ValueError(
        f"the wells' default focal heights differ ({sorted(heights)} mm); give focal_height"
      )
    return heights.pop()

  @staticmethod
  def _get_well_mask(plate: Plate, wells: Optional[Sequence[Well]]) -> Optional[bytes]:
    """The well mask of ``wells`` on ``plate``; None for every well."""
    if wells is None:
      return None
    return encode_well_mask(plate.num_items_x, plate.num_items_y, _get_measured_wells(plate, wells))

  @staticmethod
  def _check_settling_time(settling_time: float) -> None:
    """Raise unless the settling time is one the reader takes: 0.02-1.0 s (it refuses 0 and longer
    times, quit code 43)."""
    if not 0.02 <= settling_time <= 1.0:
      raise ValueError(f"settling_time must be 0.02-1.0 s, got {settling_time}")

  @staticmethod
  def _check_wells(data: MeasurementData, measured: List[Tuple[int, int]]) -> None:
    """Raise unless the reader measured as many wells as were asked for."""
    if data.wells != len(measured):
      raise RuntimeError(f"the reader measured {data.wells} wells, {len(measured)} were asked for")

  @staticmethod
  def _check_flashes(flashes: int) -> None:
    """Raise unless ``flashes`` is 1-500, the reader's maximum (it refuses more, quit code 60)."""
    if not 1 <= flashes <= 500:
      raise ValueError(f"flashes must be 1-500, got {flashes}")

  @staticmethod
  def _check_cycles(cycles: int, cycle_time: int) -> None:
    """Raise unless a kinetic read has 2-1000 cycles (the reader refuses more, quit code 60) and a
    cycle time of 1-65535 whole s."""
    if not 2 <= cycles <= 1000:
      raise ValueError(f"cycles must be 2-1000, got {cycles}")
    if not 1 <= cycle_time <= 0xFFFF:
      raise ValueError(f"cycle_time must be 1-65535 s, got {cycle_time}")

  @staticmethod
  def _get_scan_wavelengths(
    start_wavelength: int, end_wavelength: int, step_size: int
  ) -> List[int]:
    """The wavelengths of a spectrum from ``start_wavelength`` to ``end_wavelength``.

    Raises:
      ValueError: unless the step is a whole nm of at least 1 dividing the range, and the end is
        above the start.
    """
    if end_wavelength <= start_wavelength:
      raise ValueError(f"end_wavelength {end_wavelength} must be above {start_wavelength}")
    if step_size < 1 or (end_wavelength - start_wavelength) % step_size:
      raise ValueError(f"step_size {step_size} must be a whole nm of at least 1 dividing the range")
    return list(range(start_wavelength, end_wavelength + 1, step_size))
