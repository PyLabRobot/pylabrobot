"""What every measurement RUN shares: absorbance, fluorescence and luminescence alike.

A RUN's parameters start with the plate block, the scan byte, the pre-separator and `27 0f 27 0f`,
and end with the trailer, the flashes and the cycle time; the fields between are the measurement
mode's own. The reply data comes back row by row over the measured wells.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Set, Tuple

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.resources import Plate, Well

from ..errors import CommandRefusedError

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

# The well mask holds 384 bits; a 1536-well plate has no verified encoding.
_WELL_MASK_SIZE = 48

Corner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]
# Scan byte bits 6-5: the corner the scan starts at.
_CORNERS: Dict[str, int] = {"top_left": 0, "top_right": 1, "bottom_left": 2, "bottom_right": 3}

ReadingDirection = Literal["column_major", "row_major"]
# Scan byte bit 3: set reads column by column (`0a`), clear row by row (`02`).
_READING_DIRECTIONS: Dict[str, int] = {"column_major": 0x08, "row_major": 0x00}

WellScan = Literal["point", "orbital", "spiral", "matrix"]
# Pre-separator byte 0 bits: how each well is read (orbital `32`, spiral `06`, matrix `10`).
_WELL_SCANS: Dict[str, int] = {"point": 0x00, "orbital": 0x30, "spiral": 0x04, "matrix": 0x10}
# The matrix sizes, points per side.
_MATRIX_SIZES = (2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30)

ShakePattern = Literal["orbital", "linear", "double_orbital"]
# Shaking before reading, pre-separator: [12] = 02 before the (endpoint) read, [17] pattern,
# [18] speed code = rpm / 100 - 1, [20] seconds. Meander is not verified in a RUN.
_SHAKE_PATTERNS: Dict[str, int] = {"orbital": 0x00, "linear": 0x01, "double_orbital": 0x02}


# ----------------------------------------
# Settings
# ----------------------------------------


def _get_well_diameter(plate: Plate) -> float:
  """The plate's well diameter in mm: the smaller side of its first well."""
  well = plate.get_all_items()[0]
  return min(well.get_size_x(), well.get_size_y())


@dataclass
class MeasurementConfiguration:
  """Device facts every measurement mode shares."""

  settling_time_range: Tuple[float, float] = (0.0, 1.0)
  """Seconds before each well is read (0, 0.1, 0.2, 0.5 and 1.0 verified)."""
  flashes_range: Tuple[int, int] = (1, 200)
  """Flashes per well, every well scan (up to 200 per well)."""
  pause_range: Tuple[int, int] = (1, 3600)
  """Pause before reading, seconds (up to 3600 s)."""
  orbital_maximum_wells: int = 384
  """Orbital averaging works on plates of up to 384 wells (6 to 384)."""
  matrix_maximum_wells: int = 384
  """A matrix scan works on plates of up to 384 wells (up to 384)."""
  shake_speed_range: Tuple[int, int] = (100, 700)
  """Shaking before the read, rpm (100-700; 300 and 500 verified in a RUN)."""
  shake_duration_range: Tuple[int, int] = (1, 255)
  """Shaking before the read, seconds: one byte until a longer duration settles its order."""


@dataclass(frozen=True)
class ShakeSettings:
  """Shaking the plate before the read.

  Attributes:
    pattern: how the plate moves.
    speed: rpm in steps of 100, within the configuration's `shake_speed_range`.
    duration: whole seconds, within the configuration's `shake_duration_range`.

  Raises:
    ValueError: on construction, listing every field of the wrong kind (see `check` for the
      ranges, which are the reader's).
  """

  pattern: ShakePattern
  speed: int
  duration: int

  def __post_init__(self) -> None:
    errors = []
    if self.pattern not in _SHAKE_PATTERNS:
      errors.append(f"pattern must be one of {list(_SHAKE_PATTERNS)}, got {self.pattern!r}")
    if not isinstance(self.speed, int) or self.speed % 100:
      errors.append(f"speed must be whole rpm in steps of 100, is {self.speed}")
    if not isinstance(self.duration, int):
      errors.append(f"duration must be whole seconds, is {self.duration}")
    if errors:
      raise ValueError("; ".join(errors))

  def check(self, configuration: MeasurementConfiguration) -> None:
    """Raise if the reader cannot shake with these settings.

    Raises:
      ValueError: listing every field outside the configuration's range.
    """
    errors = []
    lowest, highest = configuration.shake_speed_range
    if not lowest <= self.speed <= highest:
      errors.append(f"speed must be {lowest} to {highest} rpm, is {self.speed}")
    lowest, highest = configuration.shake_duration_range
    if not lowest <= self.duration <= highest:
      errors.append(f"duration must be {lowest} to {highest} s, is {self.duration}")
    if errors:
      raise ValueError("; ".join(errors))


@dataclass(frozen=True)
class MeasurementSettings:
  """What every measurement mode reads with, in the order the reader works through it (shaking
  before the read, if any, comes first: `ShakeSettings`).

  Attributes:
    pause_before_reading: whole seconds the reader waits before reading the plate, within the
      configuration's `pause_range`; None does not wait.
    corner: the corner the scan starts at. Results are in plate order either way.
    reading_direction: column_major reads column by column; row_major row by row.
    bidirectional: read every other column or row backwards (meander); False reads all in one
      direction.
    settling_time: seconds to settle before each well, in 20 ms steps, within the
      configuration's `settling_time_range`.
    well_scan: read each well at its centre (point), or average along a circle (orbital, not on
      plates over the configuration's `orbital_maximum_wells`) or a spiral, or read it at the
      points of a square grid (matrix, not on plates over `matrix_maximum_wells`).
    scan_diameter: orbital, spiral or matrix diameter, a whole mm from 1 to the plate's well
      diameter.
    flashes: whole flashes per well (per point of a matrix), within the configuration's
      `flashes_range`.
    matrix_size: points per side of a matrix scan, one of 2 to 10, 15, 20, 25, 30; None
      for any other well scan.

  Raises:
    ValueError: on construction, listing every field of the wrong kind (see `check` for the
      rules that need the plate and the reader's ranges).
  """

  pause_before_reading: Optional[int] = None
  corner: Corner = "top_left"
  reading_direction: ReadingDirection = "column_major"
  bidirectional: bool = True
  settling_time: float = 0.1
  well_scan: WellScan = "point"
  scan_diameter: int = 3
  flashes: int = 5
  matrix_size: Optional[int] = None

  def __post_init__(self) -> None:
    """Raise listing every field of the wrong kind or combination."""
    errors = []
    pause = self.pause_before_reading
    if pause is not None and not isinstance(pause, int):
      errors.append(f"pause_before_reading must be whole seconds, is {pause}")
    if self.corner not in _CORNERS:
      errors.append(f"corner must be one of {list(_CORNERS)}, got {self.corner!r}")
    if self.reading_direction not in _READING_DIRECTIONS:
      errors.append(
        f"reading_direction must be one of {list(_READING_DIRECTIONS)}, got "
        f"{self.reading_direction!r}"
      )
    if self.well_scan not in _WELL_SCANS:
      errors.append(f"well_scan must be one of {list(_WELL_SCANS)}, got {self.well_scan!r}")
    if not isinstance(self.scan_diameter, int) or self.scan_diameter < 1:
      errors.append(f"scan_diameter must be a whole mm of at least 1, is {self.scan_diameter}")
    if not isinstance(self.flashes, int):
      errors.append(f"flashes must be whole, is {self.flashes}")
    if self.matrix_size is not None and self.well_scan != "matrix":
      errors.append(f"matrix_size is for a matrix well scan only, got {self.well_scan!r}")
    elif self.well_scan == "matrix" and (
      not isinstance(self.matrix_size, int) or self.matrix_size not in _MATRIX_SIZES
    ):
      errors.append(f"matrix_size must be one of {list(_MATRIX_SIZES)}, got {self.matrix_size!r}")
    if errors:
      raise ValueError("; ".join(errors))

  def check(
    self, plate: Plate, wells: Sequence[Well], configuration: MeasurementConfiguration
  ) -> None:
    """Raise if these settings cannot read `wells` of `plate` on the reader.

    The scan diameter's upper bound is inferred: 3 to 5 mm are verified, all on 6.58 mm wells.

    Raises:
      ValueError: listing every rule broken: a field outside the configuration's range, no wells,
        wells not on the plate, a plate over 384 wells, orbital on a plate over the
        configuration's `orbital_maximum_wells`, matrix on a plate over its
        `matrix_maximum_wells`, a scan diameter wider than the well.
    """
    errors = []
    pause = self.pause_before_reading
    lowest, highest = configuration.pause_range
    if pause is not None and not lowest <= pause <= highest:
      errors.append(f"pause_before_reading must be {lowest} to {highest} s, is {pause}")
    lowest_time, highest_time = configuration.settling_time_range
    if not lowest_time <= self.settling_time <= highest_time:
      errors.append(
        f"settling_time must be within {lowest_time} to {highest_time} s, is {self.settling_time}"
      )
    lowest, highest = configuration.flashes_range
    if not lowest <= self.flashes <= highest:
      errors.append(f"flashes must be {lowest} to {highest}, is {self.flashes}")
    if not wells:
      errors.append("no wells to measure")
    items = {id(well) for well in plate.get_all_items()}
    if any(id(well) not in items for well in wells):
      errors.append(f"every well must be on {plate.name}")
    if plate.num_items > 8 * _WELL_MASK_SIZE:
      errors.append(f"plates over {8 * _WELL_MASK_SIZE} wells are not supported")
    if self.well_scan == "orbital" and plate.num_items > configuration.orbital_maximum_wells:
      errors.append(f"orbital averaging is not available on plates of {plate.num_items} wells")
    if self.well_scan == "matrix" and plate.num_items > configuration.matrix_maximum_wells:
      errors.append(f"a matrix scan is not available on plates of {plate.num_items} wells")
    if self.well_scan != "point":
      highest = math.floor(_get_well_diameter(plate))
      if self.scan_diameter > highest:
        errors.append(f"scan_diameter must be at most {highest} mm on {plate.name}")
    if errors:
      raise ValueError("; ".join(errors))


# ----------------------------------------
# Measured wells
# ----------------------------------------


def _get_measured_identifiers(plate: Plate, wells: Sequence[Well]) -> List[str]:
  """The identifiers of the measured wells, row by row as the reader reads them out; a well
  given twice is measured once."""
  items = plate.get_all_items()
  rows = plate.num_items_y
  index = {id(well): i for i, well in enumerate(items)}
  positions = sorted({(index[id(well)] % rows, index[id(well)] // rows) for well in wells})
  return [plate.get_child_identifier(items[column * rows + row]) for row, column in positions]


def _get_readings_by_well(
  plate: Plate, wells: Sequence[Well], readings: List[float]
) -> Dict[str, float]:
  """The readings, given row by row over the measured wells, keyed by well identifier."""
  return dict(zip(_get_measured_identifiers(plate, wells), readings))


class Measurement:
  """What every measurement feature (RUN, 0x04) shares: the plate, the wells and the run itself.

  A mode adds its settings, its RUN fields and its reply decoding, and sets `_MODE`,
  `_WELL_SCAN_CODE` and `_TRAILER_START`.
  """

  # Pre-separator byte 0: the measurement mode (OR the optics and the well scan): fluorescence 00,
  # luminescence 01, absorbance 02.
  _MODE: int
  # The mode's code in the well-scan field, and the first byte of its trailer.
  _WELL_SCAN_CODE: int
  _TRAILER_START: int

  def __init__(self, driver: "CLARIOstarDriver", configuration: MeasurementConfiguration):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the measurement's device facts.
    """
    self._driver = driver
    self.configuration = configuration
    self._default_read_timeout = 600.0  # 200 flashes on 96 wells take 223 s
    self._warned_untested: Set[str] = set()

  # -- defaults and warnings -----------------------------------------------------------------------

  @property
  def default_read_timeout(self) -> float:
    """Seconds a measurement may take, from the RUN until the reader is idle."""
    return self._default_read_timeout

  @default_read_timeout.setter
  def default_read_timeout(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_read_timeout must be positive, got {value}")
    self._default_read_timeout = value

  def _warn_untested(self, what: str) -> None:
    """Warn once per reader that `what` has not run on hardware with this driver."""
    if what not in self._warned_untested:
      logger.warning(
        "%s has not been tested on hardware with this driver; please report how it works", what
      )
      self._warned_untested.add(what)

  @staticmethod
  def _warn_saturated(what: str, counts: Sequence[int], ceiling: int) -> None:
    """Warn if any of the counts of a `what` read reads the detector's ceiling."""
    saturated = sum(count >= ceiling for count in counts)
    if saturated:
      logger.warning(
        "%s: %d of %d readings read the detector's ceiling (%d); lower the gain",
        what,
        saturated,
        len(counts),
        ceiling,
      )

  # -- checks of the detector's modes, fluorescence and luminescence -------------------------------

  @staticmethod
  def _check_bandwidth(name: str, bandwidth: float, bandwidth_range: Tuple[int, int]) -> List[str]:
    """The violation of a bandwidth `name`: not a whole nm within `bandwidth_range`."""
    narrowest, widest = bandwidth_range
    if not isinstance(bandwidth, int) or not narrowest <= bandwidth <= widest:
      return [f"{name} must be a whole {narrowest} to {widest} nm, got {bandwidth!r}"]
    return []

  def _check_monochromator(self, what: str) -> List[str]:
    """The violation of `what` through a monochromator on a reader that reports none.

    The monochromators are linear variable filters. Refused only when the reader reports them
    absent; unknown lets the read through. One bit for excitation, dichroic and emission
    together; read as all or none (no reader without them is verified).
    """
    if self._driver.configuration.linear_variable_filters is False:
      return [f"the reader reports no monochromator, which {what} needs"]
    return []

  @staticmethod
  def _check_gain(gain: int, gain_range: Tuple[int, int]) -> List[str]:
    """The violation of a gain: not whole within `gain_range`."""
    lowest_gain, highest_gain = gain_range
    if not isinstance(gain, int):
      return [f"gain must be whole, got {gain!r}"]
    if not lowest_gain <= gain <= highest_gain:
      return [f"gain must be {lowest_gain} to {highest_gain}, got {gain}"]
    return []

  @staticmethod
  def _check_focal_height(
    focal_height: float, focal_height_range: Tuple[float, float]
  ) -> List[str]:
    """The violation of a focal height: not in 0.1 mm steps within `focal_height_range`."""
    lowest_height, highest_height = focal_height_range
    if (
      not lowest_height <= focal_height <= highest_height
      or abs(focal_height * 10 - round(focal_height * 10)) > 1e-9
    ):
      return [
        f"focal_height must be {lowest_height} to {highest_height} mm in 0.1 mm steps, got "
        f"{focal_height}"
      ]
    return []

  @staticmethod
  def _check_default_focal_height_percent(value: float) -> None:
    """Raise unless `value` is a percent of the well depth: 0 to 100."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
      raise ValueError(
        f"default_focal_height_percent_of_well_depth must be 0 to 100 %, got {value!r}"
      )

  @classmethod
  def _compute_default_focal_height(
    cls, wells: Sequence[Well], percent: float, focal_height_range: Tuple[float, float]
  ) -> float:
    """The focal height `percent` of the depth up `wells`, mm above the plate's base.

    The reader's focal height is the height above the plate's base: the well's location, its
    floor and the percent of its depth, rounded to 0.1 mm.

    Raises:
      ValueError: if a well has no location or floor thickness, the wells differ in that height,
        or it is outside `focal_height_range`.
    """
    heights = set()
    for well in wells:
      if well.location is None:
        raise ValueError(f"well {well.name} has no location on its plate")
      try:
        floor = well.material_z_thickness
      except NotImplementedError as error:
        raise ValueError(f"well {well.name} has no floor thickness; give focal_height") from error
      depth = well.get_size_z() * percent / 100
      heights.add(round(well.location.z + floor + depth, 1))
    if len(heights) > 1:
      raise ValueError(
        f"the wells' default focal heights differ ({sorted(heights)} mm); give focal_height"
      )
    focal_height = heights.pop()
    errors = cls._check_focal_height(focal_height, focal_height_range)
    if errors:
      raise ValueError(f"the default focal height of the wells: {errors[0]}; give focal_height")
    return focal_height

  # -- plate and wells -----------------------------------------------------------------------------

  def _get_plate_and_wells(
    self,
    settings: MeasurementSettings,
    shake: Optional[ShakeSettings],
    wells: Optional[List[Well]],
  ) -> Tuple[Plate, List[Well]]:
    """The plate in the plate holder and the wells to read (every well when None), the settings
    checked against the plate and the configuration.

    Raises:
      RuntimeError: if no plate is in the plate holder.
      ValueError: if the settings cannot read the wells of the plate on the reader
        (`MeasurementSettings.check`, `ShakeSettings.check`).
    """
    plate_holder = self._driver.drawer.plate_holder
    if plate_holder is None:
      raise RuntimeError("the drawer has no plate holder; build the reader as a CLARIOstarDevice")
    if not isinstance(plate_holder.resource, Plate):
      raise RuntimeError(f"no plate in {plate_holder.name}")
    plate = plate_holder.resource
    wells = plate.get_all_items() if wells is None else wells
    settings.check(plate, wells, self.configuration)
    if shake is not None:
      shake.check(self.configuration)
    return plate, wells

  # ----------------------------------------
  # RUN parameters
  # ----------------------------------------

  # -- plate block ---------------------------------------------------------------------------------

  @staticmethod
  def _encode_plate_geometry(plate: Plate) -> bytes:
    """The plate's 14 bytes: size, first and last well centre in 0.01 mm (u16 BE), columns, rows.

    Y is measured from the plate's back edge.
    """
    items = plate.get_all_items()

    def center_from_back(well: Well):
      if well.location is None:
        raise ValueError(f"well {well.name} has no location on {plate.name}")
      center = well.location + well.center()
      return center.x, plate.get_size_y() - center.y

    first_x, first_y = center_from_back(items[0])
    last_x, last_y = center_from_back(items[-1])
    geometry = (plate.get_size_x(), plate.get_size_y(), first_x, first_y, last_x, last_y)
    block = b"".join(round(value * 100).to_bytes(2, "big") for value in geometry)
    return block + bytes([plate.num_items_x, plate.num_items_y])

  @staticmethod
  def _encode_well_mask(plate: Plate, wells: Sequence[Well]) -> bytes:
    """The 48-byte well mask, a bit per well of `wells`.

    The bit of a well is row * columns + column, most significant bit first.
    """
    items = plate.get_all_items()
    columns, rows = plate.num_items_x, plate.num_items_y
    mask = bytearray(_WELL_MASK_SIZE)
    selected = {id(well) for well in wells}
    for index, well in enumerate(items):  # column-major: A1, B1, ..., A2, ...
      if id(well) in selected:
        bit = (index % rows) * columns + index // rows
        mask[bit // 8] |= 0x80 >> (bit % 8)
    return bytes(mask)

  def _encode_plate_block(self, plate: Plate, wells: Sequence[Well]) -> bytes:
    """The 63-byte plate block: `_encode_plate_geometry`, `00`, then `_encode_well_mask`."""
    return self._encode_plate_geometry(plate) + b"\x00" + self._encode_well_mask(plate, wells)

  # -- scan and settling bytes ---------------------------------------------------------------------

  # Scan byte bit 2: flying mode. Bit 4, equidistant kinetic cycles, stays 0.
  _FLYING_MODE = 0x04

  def _compute_scan_byte(self, settings: MeasurementSettings, *, flying_mode: bool = False) -> int:
    """The scan byte of a read; `flying_mode` sets bit 2.

    Bit 7 unidirectional, bits 6-5 the start corner, bit 3 column-major, bit 2 flying mode, bit 1
    set. Verified: `0a` (default), `2a`, `4a`, `6a` (corners), `8a` (unidirectional), `02`
    (row-major), `0e` (flying mode).
    """
    return (
      (0 if settings.bidirectional else 0x80)
      | _CORNERS[settings.corner] << 5
      | _READING_DIRECTIONS[settings.reading_direction]
      | (self._FLYING_MODE if flying_mode else 0)
      | 0x02
    )

  @staticmethod
  def _compute_settling_byte(settling_time: float) -> int:
    """Settling time in 20 ms steps (x 50), at least 1: 0, 0.1, 0.5 s -> `01`, `05`, `19`."""
    return max(round(settling_time * 50), 1)

  # -- pre-separator and well scan -----------------------------------------------------------------

  # Pre-separator byte 1: enhanced dynamic range.
  _ENHANCED_DYNAMIC_RANGE = 0x40

  def _compute_pre_separator(
    self,
    settings: MeasurementSettings,
    shake: Optional[ShakeSettings],
    *,
    optics: int = 0,
    enhanced_dynamic_range: bool = False,
  ) -> bytes:
    """The 31-byte pre-separator of a read; `enhanced_dynamic_range` sets byte 1.

    Byte 0 the mode OR `optics` OR the well scan, byte 1 enhanced dynamic range, bytes 12-20
    shaking before the read.
    """
    block = bytearray(31)
    block[0] = self._MODE | optics | _WELL_SCANS[settings.well_scan]
    if enhanced_dynamic_range:
      block[1] = self._ENHANCED_DYNAMIC_RANGE
    if shake is not None:
      block[12] = 0x02
      block[17] = _SHAKE_PATTERNS[shake.pattern]
      block[18] = shake.speed // 100 - 1
      block[20] = shake.duration
    return bytes(block)

  def _compute_well_scan_field(self, plate: Plate, settings: MeasurementSettings) -> bytes:
    """The field after `27 0f 27 0f` of an orbital, spiral or matrix read; none for a point read.

    `[_WELL_SCAN_CODE, scan diameter in mm, well diameter u16 BE in 0.01 mm, 00]`; for a matrix,
    the matrix size in place of the code.
    """
    if settings.well_scan == "point":
      return b""
    matrix_size = settings.matrix_size
    if settings.well_scan == "matrix" and matrix_size is not None:
      first = matrix_size
    else:
      first = self._WELL_SCAN_CODE
    well_diameter = round(_get_well_diameter(plate) * 100)
    return bytes([first, settings.scan_diameter]) + well_diameter.to_bytes(2, "big") + b"\x00"

  # -- start and end -------------------------------------------------------------------------------

  _SEPARATOR = b"\x27\x0f\x27\x0f"
  # Trailer after the mode's own fields, without shaking between readings; byte 0 is the mode's.
  _TRAILER_REST = bytes.fromhex("000000000001000000")

  def _compute_run_start(
    self,
    plate: Plate,
    wells: Sequence[Well],
    settings: MeasurementSettings,
    shake: Optional[ShakeSettings],
    *,
    optics: int = 0,
    flying_mode: bool = False,
    enhanced_dynamic_range: bool = False,
    scan_byte: Optional[int] = None,
  ) -> bytes:
    """The RUN parameters before the mode's own fields.

    The plate block, scan byte, pre-separator, `27 0f 27 0f`, the well-scan field and the settling
    byte. `optics` is ORed into the mode; `flying_mode` and `enhanced_dynamic_range` set their bits
    (`_compute_scan_byte`, `_compute_pre_separator`); `scan_byte` replaces the derived one.
    """
    if scan_byte is None:
      scan_byte = self._compute_scan_byte(settings, flying_mode=flying_mode)
    return (
      self._encode_plate_block(plate, wells)
      + bytes([scan_byte])
      + self._compute_pre_separator(
        settings, shake, optics=optics, enhanced_dynamic_range=enhanced_dynamic_range
      )
      + self._SEPARATOR
      + self._compute_well_scan_field(plate, settings)
      + bytes([self._compute_settling_byte(settings.settling_time)])
    )

  def _compute_run_end(self, flashes: int) -> bytes:
    """The RUN parameters of an endpoint read after the mode's own fields.

    `_TRAILER_START`, the trailer, 1 cycle, the flashes (u16 BE), a 1 s cycle time, `00`.
    """
    return (
      bytes([self._TRAILER_START])
      + self._TRAILER_REST
      + b"\x01"
      + flashes.to_bytes(2, "big")
      + (1).to_bytes(2, "big")
      + b"\x00"
    )

  # ----------------------------------------
  # Running a measurement
  # ----------------------------------------

  # Status reply byte 2 bit 0: the read's data is complete and unread; the whole-data-set read
  # clears it (every cycle of a kinetic read too).
  _DATA_READY = 0x01

  async def _unchecked_fw_run(self, parameters: bytes) -> None:
    """Send RUN with `parameters`, the bytes after the family byte."""
    await self._driver.send_command(self._driver.CommandFamily.RUN, parameters=parameters)

  async def _request_page(self, retries: int) -> bytes:
    """Request one data page (`05 02 00 00 00 00 00`) while the reader may still be busy.

    Args:
      retries: attempts on a missing or malformed reply (`send_command`).
    """
    return await self._driver.send_command(
      self._driver.CommandFamily.REQUEST,
      self._driver.Command.DATA,
      parameters=bytes(5),
      when_idle=False,
      retries=retries,
    )

  async def _request_data(self, values: Optional[int]) -> List[bytes]:
    """The data replies of a finished read: one, or with `values`, pages until that many values
    are in.

    A spectrum's data comes as several data replies, its pages, each sized to fit a frame (3 to 5
    in a spectrum); each `05 02 00 00 00 00 00` returns the next.

    Raises:
      FrameError: if a page arrives short or malformed.
      ValueError: if a page holds no values.
    """
    replies: List[bytes] = []
    received = 0
    while True:
      # A request returns the read's next page, or its last once all are sent; requesting a short
      # page again would return the page after it instead, so each page is requested once.
      reply = await self._request_page(retries=1 if values is not None else 3)
      replies.append(reply)
      if values is None:
        return replies
      count = int.from_bytes(reply[7:9], "big") if len(reply) >= 9 else 0
      if count == 0:
        raise ValueError(f"data page {len(replies)} holds no values")
      received += count
      if received >= values:
        return replies

  async def _drain_and_stop(self, what: str) -> None:
    """Drain the reply still streaming, send STOP (`0b 00`) and drop what STOP left.

    A refused STOP only warns.

    Args:
      what: what is stopped, for the log.
    """
    logger.warning("stopping the %s", what)
    try:
      # The reader sends a reply it has started to its end: drain it before STOP, and drop
      # whatever STOP left after it, so the next command reads its own reply.
      await self._driver._drain_rx()
      await self._driver.send_command(
        self._driver.CommandFamily.STOP, parameters=b"\x00", when_idle=False
      )
      await self._driver._purge_rx()
    except (CommandRefusedError, FrameError) as e:  # e.g. the run ended meanwhile (214)
      logger.warning("STOP not accepted: %s", e)

  async def _run_measurement(
    self,
    parameters: bytes,
    timeout: float,
    values: Optional[int] = None,
    *,
    progressive_pages: Optional[int] = None,
  ) -> Tuple[List[bytes], datetime]:
    """Send RUN and return the data replies, and when they were read.

    A read ends with status until the reader reports the data ready, then REQUEST DATA
    for the whole data set (`05 02 00 00 00 00 00`), once or once per page, then status until idle.
    Data that comes a page at a time while the run continues is requested whenever the data ready
    bit is set, which each request clears, until all its pages are in.
    Any failure, a timeout or a cancelled call stops the run (`0b 00`) first.

    Args:
      parameters: the RUN parameters, after the family byte.
      timeout: seconds the run may take, from the RUN until the reader is idle.
      values: the values the read returns, for data that comes in pages; None for one reply.
      progressive_pages: the pages of data that comes a page at a time during the run; None
        otherwise.

    Raises:
      TimeoutError: if the run takes longer than `timeout` seconds.
      RuntimeError: if the run ends without reporting its data ready, or before all its pages.
      ValueError: if a data page holds no values.
    """
    await self._unchecked_fw_run(parameters)
    replies: Optional[List[bytes]] = None
    pages: List[bytes] = []
    read_at = datetime.now()
    start = time.monotonic()
    try:
      # A poll sent right after the RUN's reply goes unanswered, as the reader answered: wait
      # one poll interval first.
      await asyncio.sleep(self._driver.default_poll_interval)
      while True:
        status = await self._driver.send_command(self._driver.CommandFamily.STATUS, retries=5)
        busy = self._driver._parse_status(status).busy
        ready = len(status) > 2 and bool(status[2] & self._DATA_READY)
        if progressive_pages is not None:
          # A request after the last page returns that page again: none is sent.
          if ready and len(pages) < progressive_pages:
            pages.append(await self._request_page(retries=1))
            read_at = datetime.now()
          if len(pages) == progressive_pages:
            replies = pages
          elif not busy and not ready:
            raise RuntimeError(f"the run ended with {len(pages)} of {progressive_pages} pages")
        elif ready and replies is None:
          replies = await self._request_data(values)
          read_at = datetime.now()
        if replies is not None and not busy:
          return replies, read_at
        if not busy and not ready:
          raise RuntimeError("the run ended without reporting its data ready")
        if time.monotonic() - start >= timeout:
          raise TimeoutError(f"the measurement took longer than {timeout} s")
        await asyncio.sleep(self._driver.default_poll_interval)
    except BaseException:  # a timeout, a cancelled call, a bad reply: stop the run first
      await self._drain_and_stop("measurement run")
      raise
