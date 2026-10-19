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
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Sequence, Tuple

from pylabrobot.resources import Plate, Well

from ..errors import CommandRefusedError, FrameError

if TYPE_CHECKING:
  from ..master import CLARIOstarDriver

logger = logging.getLogger(__name__)

# The well mask holds 384 bits; a 1536-well plate has no captured encoding.
_WELL_MASK_SIZE = 48

Corner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]
# Scan byte bits 6-5: the corner the scan starts at (C01-C03).
_CORNERS: Dict[str, int] = {"top_left": 0, "top_right": 1, "bottom_left": 2, "bottom_right": 3}

WellScan = Literal["point", "orbital", "spiral"]
# Pre-separator byte 0 bits: how each well is read (A02 orbital `32`, B1 spiral `06`).
_WELL_SCANS: Dict[str, int] = {"point": 0x00, "orbital": 0x30, "spiral": 0x04}

ShakePattern = Literal["orbital", "linear", "double_orbital"]
# Shaking before reading, pre-separator: [12] = 02 before the (endpoint) read, [17] pattern,
# [18] speed code = rpm / 100 - 1, [20] seconds (F01-F06). Meander is not captured in a RUN.
_SHAKE_PATTERNS: Dict[str, int] = {"orbital": 0x00, "linear": 0x01, "double_orbital": 0x02}

# Status reply byte 2 bit 0: the read's data is complete and unread; the whole-data-set read
# clears it (A03; every cycle of DOE_KIN_DATA01).
_DATA_READY = 0x01

# Pre-separator byte 0: the measurement mode (OR the well scan mode and the optics).
MODE_FLUORESCENCE = 0x00
MODE_LUMINESCENCE = 0x01
MODE_ABSORBANCE = 0x02

_SEPARATOR = b"\x27\x0f\x27\x0f"
# Trailer after the mode's own fields, without shaking between readings; byte 0 is the mode's.
_TRAILER_REST = bytes.fromhex("000000000001000000")


def _encode_plate_block(plate: Plate, wells: Sequence[Well]) -> bytes:
  """The 63-byte plate block: geometry in 0.01 mm (u16 BE), columns, rows, `00`, well mask.

  Y is measured from the plate's back edge. The mask bit of a well is row * columns + column,
  most significant bit first.
  """
  items = plate.get_all_items()
  columns, rows = plate.num_items_x, plate.num_items_y

  def center_from_back(well: Well):
    if well.location is None:
      raise ValueError(f"well {well.name} has no location on {plate.name}")
    center = well.location + well.center()
    return center.x, plate.get_size_y() - center.y

  first_x, first_y = center_from_back(items[0])
  last_x, last_y = center_from_back(items[-1])
  geometry = (plate.get_size_x(), plate.get_size_y(), first_x, first_y, last_x, last_y)
  block = b"".join(round(value * 100).to_bytes(2, "big") for value in geometry)
  block += bytes([columns, rows, 0x00])

  mask = bytearray(_WELL_MASK_SIZE)
  selected = {id(well) for well in wells}
  for index, well in enumerate(items):  # column-major: A1, B1, ..., A2, ...
    if id(well) in selected:
      bit = (index % rows) * columns + index // rows
      mask[bit // 8] |= 0x80 >> (bit % 8)
  return block + bytes(mask)


def _compute_scan_byte(bidirectional: bool, vertical: bool, corner: Corner) -> int:
  """The scan byte: bit 7 unidirectional, bits 6-5 the start corner, bit 3 vertical, bit 1 set.

  Captured: `0a` (default), `2a`, `4a`, `6a` (corners), `8a` (unidirectional), `02` (horizontal).
  """
  return (0 if bidirectional else 0x80) | _CORNERS[corner] << 5 | (0x08 if vertical else 0) | 0x02


def _compute_settling_byte(settling_time: float) -> int:
  """Settling time in 20 ms steps (x 50), at least 1: 0, 0.1, 0.5 s -> `01`, `05`, `19`."""
  return max(round(settling_time * 50), 1)


def _get_well_diameter(plate: Plate) -> float:
  """The plate's well diameter in mm: the smaller side of its first well."""
  well = plate.get_all_items()[0]
  return min(well.get_size_x(), well.get_size_y())


@dataclass
class MeasurementConfiguration:
  """Device facts every measurement mode shares."""

  settling_time_range: Tuple[float, float] = (0.0, 1.0)
  """Seconds before each well is read (0, 0.1, 0.2, 0.5 and 1.0 captured)."""
  flashes_range: Tuple[int, int] = (1, 200)
  """Flashes per well, every well scan (the vendor software manual: up to 200 per well)."""
  pause_range: Tuple[int, int] = (1, 3600)
  """Pause before reading, seconds (the vendor software manual: up to 3600 s)."""
  orbital_maximum_wells: int = 384
  """Orbital averaging works on plates of up to 384 wells (the vendor software manual: 6 to 384)."""
  shake_speed_range: Tuple[int, int] = (100, 700)
  """Shaking before the read, rpm (the standalone shake's captured 100-700; 300, 500 in a RUN)."""
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
    vertical: read column by column; False reads row by row.
    bidirectional: read every other column or row backwards (meander); False reads all in one
      direction.
    settling_time: seconds to settle before each well, in 20 ms steps, within the
      configuration's `settling_time_range`.
    well_scan: read each well at its centre (point), or average along a circle (orbital, not on
      plates over the configuration's `orbital_maximum_wells`) or a spiral.
    scan_diameter: orbital or spiral diameter, a whole mm from 1 to the plate's well diameter.
    flashes: whole flashes per well, within the configuration's `flashes_range`.

  Raises:
    ValueError: on construction, listing every field of the wrong kind (see `check` for the
      rules that need the plate and the reader's ranges).
  """

  pause_before_reading: Optional[int] = None
  corner: Corner = "top_left"
  vertical: bool = True
  bidirectional: bool = True
  settling_time: float = 0.1  # the vendor's default
  well_scan: WellScan = "point"
  scan_diameter: int = 3  # the vendor's (A02, B2)
  flashes: int = 5  # as the captured default endpoint read (DOE_Baseline)

  def __post_init__(self) -> None:
    errors = []
    pause = self.pause_before_reading
    if pause is not None and not isinstance(pause, int):
      errors.append(f"pause_before_reading must be whole seconds, is {pause}")
    if self.corner not in _CORNERS:
      errors.append(f"corner must be one of {list(_CORNERS)}, got {self.corner!r}")
    if self.well_scan not in _WELL_SCANS:
      errors.append(f"well_scan must be one of {list(_WELL_SCANS)}, got {self.well_scan!r}")
    if not isinstance(self.scan_diameter, int) or self.scan_diameter < 1:
      errors.append(f"scan_diameter must be a whole mm of at least 1, is {self.scan_diameter}")
    if not isinstance(self.flashes, int):
      errors.append(f"flashes must be whole, is {self.flashes}")
    if errors:
      raise ValueError("; ".join(errors))

  def check(
    self, plate: Plate, wells: Sequence[Well], configuration: MeasurementConfiguration
  ) -> None:
    """Raise if these settings cannot read `wells` of `plate` on the reader.

    The scan diameter's upper bound is inferred: 3 to 5 mm are captured, all on 6.58 mm wells.

    Raises:
      ValueError: listing every rule broken: a field outside the configuration's range, no wells,
        wells not on the plate, a plate over 384 wells, orbital on a plate over the
        configuration's `orbital_maximum_wells`, a scan diameter wider than the well.
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
    if self.well_scan != "point":
      highest = math.floor(_get_well_diameter(plate))
      if self.scan_diameter > highest:
        errors.append(f"scan_diameter must be at most {highest} mm on {plate.name}")
    if errors:
      raise ValueError("; ".join(errors))


def _compute_pre_separator(
  mode: int, settings: MeasurementSettings, shake: Optional[ShakeSettings]
) -> bytes:
  """The 31-byte pre-separator: mode and well scan (top optics), and shaking before the read."""
  block = bytearray(31)
  block[0] = mode | _WELL_SCANS[settings.well_scan]
  if shake is not None:
    block[12] = 0x02
    block[17] = _SHAKE_PATTERNS[shake.pattern]
    block[18] = shake.speed // 100 - 1
    block[20] = shake.duration
  return bytes(block)


def _compute_well_scan_field(plate: Plate, code: int, settings: MeasurementSettings) -> bytes:
  """The field after `27 0f 27 0f` for an orbital or spiral read, none for a point read:
  `[code, scan diameter in mm, well diameter u16 BE in 0.01 mm, 00]` (A02, B1)."""
  if settings.well_scan == "point":
    return b""
  well_diameter = round(_get_well_diameter(plate) * 100)
  return bytes([code, settings.scan_diameter]) + well_diameter.to_bytes(2, "big") + b"\x00"


def _compute_run_start(
  plate: Plate,
  wells: Sequence[Well],
  mode: int,
  well_scan_code: int,
  settings: MeasurementSettings,
  shake: Optional[ShakeSettings],
) -> bytes:
  """The RUN parameters before the mode's own fields: plate block, scan byte, pre-separator,
  `27 0f 27 0f`, the well-scan field and the settling byte."""
  return (
    _encode_plate_block(plate, wells)
    + bytes([_compute_scan_byte(settings.bidirectional, settings.vertical, settings.corner)])
    + _compute_pre_separator(mode, settings, shake)
    + _SEPARATOR
    + _compute_well_scan_field(plate, well_scan_code, settings)
    + bytes([_compute_settling_byte(settings.settling_time)])
  )


def _compute_run_end(trailer_start: int, flashes: int) -> bytes:
  """The RUN parameters of an endpoint read after the mode's own fields: trailer, 1 cycle, the
  flashes (u16 BE), a 1 s cycle time, `00` (DOE_Baseline)."""
  return (
    bytes([trailer_start])
    + _TRAILER_REST
    + b"\x01"
    + flashes.to_bytes(2, "big")
    + (1).to_bytes(2, "big")
    + b"\x00"
  )


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
  """What every measurement feature (RUN command family, 0x04) shares: the plate, the wells and
  the run itself. A mode adds its settings, its RUN fields and its reply decoding."""

  def __init__(self, driver: "CLARIOstarDriver", configuration: MeasurementConfiguration):
    """
    Args:
      driver: the driver to send commands through.
      configuration: the measurement's device facts.
    """
    self._driver = driver
    self.configuration = configuration
    self._default_read_timeout = 600.0  # 200 flashes on 96 wells take 223 s

  @property
  def default_read_timeout(self) -> float:
    """Seconds a measurement may take, from the RUN until the reader is idle."""
    return self._default_read_timeout

  @default_read_timeout.setter
  def default_read_timeout(self, value: float) -> None:
    if not value > 0:
      raise ValueError(f"default_read_timeout must be positive, got {value}")
    self._default_read_timeout = value

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

  async def _unchecked_fw_run(self, parameters: bytes) -> None:
    """Send RUN with `parameters`, the bytes after the family byte."""
    await self._driver.send_command(self._driver.CommandFamily.RUN, parameters=parameters)

  async def _request_data(self, values: Optional[int]) -> List[bytes]:
    """The data replies of a finished read: one, or with `values`, pages until that many values
    are in.

    A spectrum's data comes as several data replies, its pages, each sized to fit a frame (H01: 3,
    the 220-1000 nm verification read: 5); each `05 02 00 00 00 00 00` returns the next.

    Raises:
      FrameError: if a page arrives short or malformed.
      ValueError: if a page holds no values.
    """
    replies: List[bytes] = []
    received = 0
    while True:
      # A request returns the read's next page, or its last once all are sent; requesting a short
      # page again would return the page after it instead, so each page is requested once.
      reply = await self._driver.send_command(
        self._driver.CommandFamily.REQUEST,
        self._driver.Command.DATA,
        parameters=bytes(5),
        when_idle=False,
        retries=1 if values is not None else 3,
      )
      replies.append(reply)
      if values is None:
        return replies
      count = int.from_bytes(reply[7:9], "big") if len(reply) >= 9 else 0
      if count == 0:
        raise ValueError(f"data page {len(replies)} holds no values")
      received += count
      if received >= values:
        return replies

  async def _run_measurement(
    self, parameters: bytes, timeout: float, values: Optional[int] = None
  ) -> Tuple[List[bytes], datetime]:
    """Send RUN and return the data replies, and when they were read.

    As the vendor ends a read: status until the reader reports the data ready, then REQUEST DATA
    for the whole data set (`05 02 00 00 00 00 00`), once or once per page, then status until idle.
    Any failure, a timeout or a cancelled call stops the run (`0b 00`) first.

    Args:
      parameters: the RUN parameters, after the family byte.
      timeout: seconds the run may take, from the RUN until the reader is idle.
      values: the values the read returns, for data that comes in pages; None for one reply.

    Raises:
      TimeoutError: if the run takes longer than `timeout` seconds.
      RuntimeError: if the run ends without reporting its data ready.
      ValueError: if a data page holds no values.
    """
    await self._unchecked_fw_run(parameters)
    replies: Optional[List[bytes]] = None
    read_at = datetime.now()
    start = time.monotonic()
    try:
      while True:
        status = await self._driver.send_command(self._driver.CommandFamily.STATUS, retries=5)
        busy = self._driver._parse_status(status).busy
        ready = len(status) > 2 and bool(status[2] & _DATA_READY)
        if ready and replies is None:
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
      logger.warning("stopping the measurement run")
      try:
        # A cancel between a write and its read leaves that reply unread: drop it before STOP,
        # and drop whatever STOP left after it, so the next command reads its own reply.
        await self._driver._purge_rx()
        await self._driver.send_command(
          self._driver.CommandFamily.STOP, parameters=b"\x00", when_idle=False
        )
        await self._driver._purge_rx()
      except (CommandRefusedError, FrameError) as e:  # e.g. the run ended meanwhile (214)
        logger.warning("STOP not accepted: %s", e)
      raise
