"""Tecan Infinite M1000 Pro plate reader."""

import asyncio
import logging
import math
import re
import statistics
import struct
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import reduce
from operator import xor
from pathlib import Path
from typing import Any, Literal

from pylabrobot.io.usb import USB
from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)

OVER = math.inf
"""Value reported for a saturated well (i-control prints ``OVER``)."""


def _format_absorbance_result(
  plate: Plate,
  wells: Sequence[Well],
  values: Sequence[float],
  wavelength: int,
  start: datetime,
  end: datetime,
) -> AbsorbanceResult:
  """Place already-calculated measurements in PLR's shared absorbance result."""
  if len(wells) != len(values):
    raise ValueError("A value is required for each well")
  if any(well.parent is not plate for well in wells):
    raise ValueError("Every well must belong to the specified plate")
  entries = sorted(zip(wells, values), key=lambda pair: (pair[0].get_row(), pair[0].get_column()))
  data = {well.get_identifier(): value for well, value in entries}
  if len(data) != len(wells):
    raise ValueError("Duplicate wells are not allowed")
  return AbsorbanceResult(
    data=data,
    wavelength=wavelength,
    temperature=None,
    start_timestamp=start,
    end_timestamp=end,
  )


def _decode_binary_block(block: bytes, payload_size: int, *, expected_sequence: int = 0) -> bytes:
  """Check a binary block's payload checksum and sequence."""
  if payload_size < 0 or len(block) != payload_size + 4:
    raise ValueError("Invalid binary block length")
  payload = block[:payload_size]
  if reduce(xor, payload, 0) != block[-1]:
    raise ValueError("Binary block checksum mismatch")
  if block[payload_size] != expected_sequence:
    raise ValueError("Binary block sequence mismatch")
  if int.from_bytes(block[payload_size + 1 : payload_size + 3], "big") != payload_size:
    raise ValueError("Binary block length mismatch")
  return payload


@dataclass(frozen=True)
class _BinaryDataHeader:
  """Raw reader binary packet header fields."""

  flags: int
  filler: int
  data_size: int
  max_block_size: int
  data_type: int
  timeout_raw: int


def _decode_binary_header(header: bytes) -> _BinaryDataHeader:
  """Decode a 20-byte reader binary header; units and packet types remain device-specific."""
  if len(header) != 20:
    raise ValueError("Invalid binary header length")
  identifier, flags, size, filler, data_size, max_block, data_type, timeout = struct.unpack(
    ">HHBBIIHI", header
  )
  if identifier != 0x0100 or size != len(header):
    raise ValueError("Invalid binary header identifier or size")
  return _BinaryDataHeader(flags, filler, data_size, max_block, data_type, timeout)


def _validate_absorbance_burst(events: Sequence[str | bytes], *, expected_type: int) -> list[bytes]:
  """Validate one completed binary burst before decoding measurement data."""
  meaningful = [
    event for event in events if not (isinstance(event, str) and event.startswith(("MSG", "LOG:")))
  ]
  if not meaningful or meaningful[-1] != "ST":
    raise ValueError("Binary burst is missing its terminal standby response")
  body = meaningful[:-1]
  if len(body) < 4 or len(body) % 2:
    raise ValueError("Binary burst has incomplete frame pairs")
  blocks: list[bytes] = []
  for i in range(0, len(body), 2):
    announcement, payload = body[i], body[i + 1]
    if not isinstance(announcement, str) or not isinstance(payload, bytes):
      raise TypeError("Unexpected binary burst event order")
    match = re.fullmatch(r"([0-9]+),BIN:", announcement)
    if match is None or int(match.group(1)) != len(payload):
      raise ValueError("Binary burst length announcement mismatch")
    blocks.append(payload)
  header = _decode_binary_header(blocks[0])
  if header.flags != 0 or header.data_type != expected_type:
    raise ValueError("Unexpected binary burst type or flags")
  # on-the-fly packets (type 529) exceed the declared block size (captured: 38 > 18 bytes)
  if header.max_block_size <= 0 or (
    expected_type != 529 and any(len(block) > header.max_block_size for block in blocks[1:])
  ):
    raise ValueError("Invalid binary burst block size")
  if sum(map(len, blocks[1:])) != header.data_size:
    raise ValueError("Incomplete binary burst payload")
  return blocks[1:]


@dataclass(frozen=True)
class _AbsorbancePrepareItem4Channel:
  """Raw preparation values for four measurement channels."""

  ticker_overflows: int
  ticker_counter: int
  measurement_gain: tuple[int, int, int, int]
  measurement_dark: tuple[int, int, int, int]
  measurement_bright: tuple[int, int, int, int]
  reference_gain: int
  reference_dark: int
  reference_bright: int


@dataclass(frozen=True)
class _AbsorbancePreparePacket4Channel:
  """Preparation payload used by the device's absorbance calculation."""

  label: int
  ex_wavelength_raw: int
  items: list[_AbsorbancePrepareItem4Channel]


def _decode_absorbance_prepare_4channel(payload: bytes) -> _AbsorbancePreparePacket4Channel:
  """Decode a four-channel preparation payload without calculating absorbance."""
  if len(payload) < 4 or (len(payload) - 4) % 36:
    raise ValueError("Invalid four-channel preparation payload length")
  label, wavelength = struct.unpack_from(">HH", payload)
  items = []
  for values in struct.iter_unpack(">I16H", payload[4:]):
    (
      overflow,
      counter,
      g0,
      g1,
      g2,
      g3,
      d0,
      d1,
      d2,
      d3,
      b0,
      b1,
      b2,
      b3,
      ref_gain,
      ref_dark,
      ref_bright,
    ) = values
    items.append(
      _AbsorbancePrepareItem4Channel(
        overflow,
        counter,
        (g0, g1, g2, g3),
        (d0, d1, d2, d3),
        (b0, b1, b2, b3),
        ref_gain,
        ref_dark,
        ref_bright,
      )
    )
  return _AbsorbancePreparePacket4Channel(label, wavelength, items)


@dataclass(frozen=True)
class _AbsorbanceRawItem4Channel:
  """Uncalculated values from one four-channel absorbance data item."""

  ticker_overflows: int
  ticker_counter: int
  measurement_counts: tuple[int, int, int, int]
  reference_count: int


@dataclass(frozen=True)
class _AbsorbanceRawPacket4Channel:
  """Decoded payload, without calibration or well-position mapping."""

  label: int
  ex_wavelength_raw: int
  number_of_packets: int | None
  items: list[_AbsorbanceRawItem4Channel]


def _decode_absorbance_data_4channel(
  payload: bytes, *, multi_packet: bool = False
) -> _AbsorbanceRawPacket4Channel:
  """Decode a four-channel payload; this does not calculate absorbance."""
  header_size = 6 if multi_packet else 4
  if len(payload) < header_size or (len(payload) - header_size) % 16:
    raise ValueError("Invalid four-channel absorbance payload length")
  label, wavelength = struct.unpack_from(">HH", payload)
  number_of_packets = struct.unpack_from(">H", payload, 4)[0] if multi_packet else None
  items = []
  for overflow, counter, c0, c1, c2, c3, reference in struct.iter_unpack(
    ">I6H", payload[header_size:]
  ):
    items.append(_AbsorbanceRawItem4Channel(overflow, counter, (c0, c1, c2, c3), reference))
  return _AbsorbanceRawPacket4Channel(label, wavelength, number_of_packets, items)


def _calculate_absorbance_4channel(
  calibration: _AbsorbancePreparePacket4Channel,
  measurement: _AbsorbanceRawPacket4Channel,
  *,
  channel: int,
) -> float:
  """Calculate OD for a single raw channel; reject ambiguous or invalid samples.

  This conservative offline calculation is not validated against a reader or i-control.
  """
  if channel not in range(4):
    raise ValueError("Absorbance channel must be between 0 and 3")
  if (calibration.label, calibration.ex_wavelength_raw) != (
    measurement.label,
    measurement.ex_wavelength_raw,
  ):
    raise ValueError("Calibration and measurement label or wavelength differ")
  if not calibration.items or not measurement.items:
    raise ValueError("Absorbance calibration and measurement must not be empty")
  cal = calibration.items[0]
  dark = cal.measurement_dark[channel]
  bright = cal.measurement_bright[channel]
  if bright <= dark or cal.reference_bright <= cal.reference_dark:
    raise ValueError("Invalid absorbance calibration")
  transmissions = []
  for item in measurement.items:
    count = item.measurement_counts[channel]
    if count >= 65535 or item.reference_count >= 65535:
      return OVER
    if item.reference_count - cal.reference_dark < 500:
      return math.nan
    transmissions.append((count - dark) / (item.reference_count - cal.reference_dark))
  factor = (cal.reference_bright - cal.reference_dark) / (bright - dark)
  transmission = sum(transmissions) / len(transmissions) * factor
  if transmission <= 0:
    return OVER
  return -math.log10(transmission)


def _assemble_full_plate_absorbance(
  plate: Plate,
  calibration: _AbsorbancePreparePacket4Channel,
  packets: Sequence[_AbsorbanceRawPacket4Channel],
  wavelength: int,
  start: datetime,
  end: datetime,
) -> AbsorbanceResult:
  """Map two 12-position, four-channel scans into PLR's 96-well result."""
  if (plate.num_items_x, plate.num_items_y) != (12, 8):
    raise ValueError("A 96-well plate is required")
  if len(packets) != 24:
    raise ValueError("Expected exactly 24 four-channel scan packets")
  if calibration.ex_wavelength_raw != wavelength * 10:
    raise ValueError("Calibration wavelength differs from requested wavelength")
  wells: list[Well] = []
  values: list[float] = []
  for index, packet in enumerate(packets):
    if packet.number_of_packets is not None or len(packet.items) != 1:
      raise ValueError("Unsupported four-channel scan packet layout")
    column = index + 1 if index < 12 else 24 - index
    row_offset = 0 if index < 12 else 4
    for channel in range(4):
      well = plate.get_item(f"{'ABCDEFGH'[row_offset + channel]}{column}")
      wells.append(well)
      values.append(_calculate_absorbance_4channel(calibration, packet, channel=channel))
  return _format_absorbance_result(plate, wells, values, wavelength, start, end)


PDFX_DIRECTORY = Path(r"C:\Users\Public\Documents\Tecan\Pdfx")
"""Where i-control keeps its plate definitions (``<name>.pdfx``)."""

SHAKING_FREQUENCIES: dict[str, dict[int, int]] = {
  "orbital": dict(zip(range(1000, 6001, 500), (97, 68, 51, 43, 36, 31, 28, 25, 23, 21, 20))),
  "linear": dict(zip(range(2000, 6001, 500), (109, 93, 79, 69, 62, 56, 52, 48, 45))),
  "double": dict(zip(range(1000, 3501, 500), (51, 36, 28, 24, 20, 18))),
}
"""Amplitude (µm) -> ``SHAKING FREQUENCY`` code (rpm / 6), as the reader reports them."""

_ABSORBANCE_CHANNEL_PITCH = 9000
"""Y distance in µm between the four absorbance channels."""

_ABSORBANCE_BEAM_DIAMETER = 800
"""µm; ``BEAM DIAMETER=800`` in absorbance, used for multiple-reads geometry."""

_Z_STEP = 42
_Z_MAX = 26260
"""Z axis step and maximum in µm (``#POSITION ABS,Z`` -> ``6260~26260:42``)."""


def _round_half_up(value: float) -> int:
  return math.floor(value + 0.5)


def _trunc_div(a: float, b: int) -> int:
  """C# integer division (truncates toward zero)."""
  return int(a / b)


@dataclass(frozen=True)
class PlateGeometry:
  """Plate geometry in µm, as in i-control ``.pdfx`` plate definitions.

  The reader's X/Y motor coordinates of a well centre are the definition's first-column
  distance plus the pitch times the column or row index.
  """

  name: str
  rows: int
  columns: int
  a1_x: int
  a1_y: int
  last_x: int
  last_y: int
  height: int = 14600
  height_with_cover: int | None = None
  height_tolerance: int = 200
  well_depth: int = 10900
  well_size_x: int = 6960
  well_size_y: int = 6960
  well_bottom_x: int = 0
  well_bottom_y: int = 0

  def x(self, column: int) -> int:
    """Motor X of a 0-indexed column."""
    pitch = (self.last_x - self.a1_x) / (self.columns - 1) if self.columns > 1 else 0
    return _round_half_up(self.a1_x + column * pitch)

  def y(self, row: int) -> int:
    """Motor Y of a 0-indexed row."""
    pitch = (self.last_y - self.a1_y) / (self.rows - 1) if self.rows > 1 else 0
    return _round_half_up(self.a1_y + row * pitch)

  def absorbance_z(self, lid: bool = False) -> int:
    """i-control's absorbance Z (``AbsorbanceRatioReadingAction.DetermineZHeight``).

    (plate height, or height with cover, + height tolerance + 6000 - 0.3 * well depth),
    truncated to the 42 µm Z step and limited to the axis maximum.
    """
    if lid and self.height_with_cover is None:
      raise ValueError(f"{self.name} has no height with cover")
    height = self.height_with_cover if lid and self.height_with_cover else self.height
    z = height + self.height_tolerance + 6000 - int(self.well_depth * 0.3)
    return min(z // _Z_STEP * _Z_STEP, _Z_MAX)

  def multi_read_dimensions(self) -> tuple[int, int]:
    """Well size used for multiple reads: the bottom size when it is smaller than the top."""
    x = self.well_bottom_x if 0 < self.well_bottom_x < self.well_size_x else self.well_size_x
    y = self.well_bottom_y if 0 < self.well_bottom_y < self.well_size_y else self.well_size_y
    return x, y

  @classmethod
  def from_pdfx(cls, path: str | Path) -> "PlateGeometry":
    """Read a rectangular i-control plate definition."""
    ns = {"t": "tecan.at.schema.documents"}
    plate = ET.parse(path).getroot().find("t:TecanPlateDefinition", ns)
    base = None if plate is None else plate.find("t:DefinitionRectanglePlateBaseDimensions", ns)
    if plate is None or base is None:
      raise ValueError(f"{path} is not a rectangular plate definition")
    well = plate.find("t:DefinitionWellBaseDimensions", ns)
    w = {} if well is None else well.attrib

    def number(attributes, key, default=None):
      value = attributes.get(key, "")
      return int(value) if value.strip() else default

    return cls(
      name=plate.attrib["name"],
      rows=int(base.attrib["nrOfRows"]),
      columns=int(base.attrib["nrOfColumns"]),
      a1_x=int(base.attrib["firstColumnDistanceX"]),
      a1_y=int(base.attrib["firstColumnDistanceY"]),
      last_x=int(base.attrib["lastColumnDistanceX"]),
      last_y=int(base.attrib["lastColumnDistanceY"]),
      height=number(base.attrib, "height", 0),
      height_with_cover=number(base.attrib, "heightWithCover"),
      height_tolerance=number(base.attrib, "heightTolerance", 0),
      well_depth=number(w, "dimensionZ", 0),
      well_size_x=number(w, "dimensionX", 0),
      well_size_y=number(w, "dimensionY", 0),
      well_bottom_x=number(w, "dimensionBottomX", 0),
      well_bottom_y=number(w, "dimensionBottomY", 0),
    )


KNOWN_PLATES = {
  "GRE96ft": PlateGeometry(
    "GRE96ft", 8, 12, 14380, 11240, 113380, 74240, 14600, 17606, 200, 10900, 6960, 6960, 6390, 6390
  ),
  "GRE384ft": PlateGeometry(
    "GRE384ft", 16, 24, 12130, 8990, 115630, 76490, 14400, 17406, 200, 11500, 3700, 3700, 3300, 3300
  ),
}
"""Built-in copies of the two captured plate definitions (used when no .pdfx is installed)."""


def _resolve_geometry(plate_definition: "str | PlateGeometry") -> PlateGeometry:
  if isinstance(plate_definition, PlateGeometry):
    return plate_definition
  path = PDFX_DIRECTORY / f"{plate_definition}.pdfx"
  if path.is_file():
    return PlateGeometry.from_pdfx(path)
  if plate_definition in KNOWN_PLATES:
    return KNOWN_PLATES[plate_definition]
  raise ValueError(f"Unknown plate definition {plate_definition!r} (no {path})")


MultiReadPattern = Literal["filled_square", "square", "x_line", "y_line", "xy_lines"]


def _multi_read_runs(
  geometry: PlateGeometry, pattern: str, size: int, border: int, beam: int
) -> tuple[list[list[tuple[int, int]]], int, int]:
  """i-control's multiple-reads grid (``MultipleReadsPerWellGridIterator``).

  Returns the scan runs (grid points (i, j) along one motor Y line, in scan order) and the
  X/Y step in µm. Grid index j = 0 is the smallest motor Y; i = 0 the smallest motor X.
  """
  if size < 1:
    raise ValueError("Multiple-reads size must be at least 1")
  dim_x, dim_y = geometry.multi_read_dimensions()
  last = max(1, size - 1)
  if pattern in ("filled_square", "square"):
    side = _trunc_div((dim_x - 2 * border) * 0.707106, 1) + int(beam * 0.414213 * 0.707106)
    step_x = step_y = _trunc_div(side - beam, last)
  elif pattern in ("x_line", "y_line", "xy_lines"):
    step_x = _trunc_div(dim_x - 2 * border - beam, last)
    step_y = _trunc_div(dim_y - 2 * border - beam, last)
  else:
    raise ValueError(
      f"Multiple-reads pattern {pattern!r} is not supported (circle patterns use i-control's "
      "built-in point tables, which are not implemented)"
    )
  if step_x < 0 or step_y < 0:
    raise ValueError("The border is too large for this well")
  n, c = size, (size - 1) // 2
  runs: list[list[tuple[int, int]]]
  if pattern == "filled_square":
    runs = [[(i, j) for i in (range(n) if j % 2 == 0 else range(n - 1, -1, -1))] for j in range(n)]
  elif pattern == "square":
    runs = [[(i, 0) for i in range(n)]]
    runs += [[(n - 1, j)] for j in range(1, n - 1)]
    if n > 1:
      runs.append([(i, n - 1) for i in range(n - 1, -1, -1)])
    runs += [[(0, j)] for j in range(n - 2, 0, -1)]
  elif pattern == "x_line":
    runs = [[(i, c) for i in range(n)]]
  elif pattern == "y_line":
    runs = [[(c, j)] for j in range(n)]
  else:
    runs = [[(c, j)] for j in range(c)] + [[(i, c) for i in range(n)]]
    runs += [[(c, j)] for j in range(c + 1, n)]
  return runs, step_x, step_y


def _selected_cells(
  plate: Plate, wells: Sequence[Well] | None, geometry: PlateGeometry
) -> set[tuple[int, int]]:
  """Return (row, column) of the wells to report, checking they fit the geometry."""
  if (plate.num_items_y, plate.num_items_x) != (geometry.rows, geometry.columns):
    raise ValueError(
      f"Plate is {plate.num_items_y}x{plate.num_items_x}, "
      f"{geometry.name} is {geometry.rows}x{geometry.columns}"
    )
  if wells is None:
    wells = plate.get_all_items()
  if not wells:
    raise ValueError("No wells selected")
  if any(well.parent is not plate for well in wells):
    raise ValueError("Every well must belong to the specified plate")
  cells = {(well.get_row(), well.get_column()) for well in wells}
  if len(cells) != len(wells):
    raise ValueError("Duplicate wells are not allowed")
  return cells


def _tenths(value: float) -> int:
  """nm -> the reader's 0.1 nm units; rejects finer resolution."""
  tenths = round(value * 10)
  if abs(tenths - value * 10) > 1e-6 or tenths <= 0:
    raise ValueError(f"{value} nm is not a positive multiple of 0.1 nm")
  return tenths


def _check_reads(flashes: int, settle_time: int) -> None:
  if not isinstance(flashes, int) or flashes < 1:
    raise ValueError("flashes must be a positive integer")
  if not isinstance(settle_time, int) or settle_time < 0:
    raise ValueError("settle_time must be a non-negative whole number of ms")


@dataclass(frozen=True)
class _ScanPass:
  """One Y position of the read head: plate row per channel and columns in scan order."""

  y: int
  rows: tuple[int | None, ...]
  columns: tuple[int, ...]


def _plan_passes(
  geometry: PlateGeometry, cells: set[tuple[int, int]], *, channels: int
) -> list[_ScanPass]:
  """Plan i-control's serpentine passes for the selected cells.

  The four absorbance channels are 9 mm apart, so on a plate with row pitch p they read
  rows r, r + k, r + 2k, r + 3k with k = 9000 / p (1 on 96-well, 2 on 384-well plates).
  Each pass scans the columns between the first and last selected column of its rows.
  """
  if channels == 1:
    stride = 1
  else:
    pitch = (geometry.y(geometry.rows - 1) - geometry.a1_y) / max(geometry.rows - 1, 1)
    stride = round(_ABSORBANCE_CHANNEL_PITCH / pitch) if pitch else 0
    if stride < 1 or abs(stride * pitch - _ABSORBANCE_CHANNEL_PITCH) > 50:
      raise ValueError(f"{geometry.name} row pitch does not fit the 9 mm channel spacing")
  passes: list[_ScanPass] = []
  block = channels * stride
  for first in range(0, geometry.rows, block):
    for offset in range(stride):
      base = first + offset
      rows = tuple(
        base + c * stride if base + c * stride < geometry.rows else None for c in range(channels)
      )
      columns = sorted(column for row, column in cells if row in rows)
      if not columns:
        continue
      span = tuple(range(columns[0], columns[-1] + 1))
      if len(passes) % 2:
        span = span[::-1]
      passes.append(_ScanPass(geometry.y(base), rows, span))
  return passes


@dataclass
class InfiniteM1000ProAbsorbanceResult(AbsorbanceResult):
  """Absorbance result with i-control's multiple-reads-per-well detail.

  Attributes:
    standard_deviation: sample standard deviation per well (multiple reads only).
    reads: each well's reads keyed by i-control's ``"x;y"`` grid label (multiple reads only).
  """

  standard_deviation: dict[str, float] = field(default_factory=dict, kw_only=True)
  reads: dict[str, dict[str, float]] = field(default_factory=dict, kw_only=True)
  measurement: dict[str, float] = field(default_factory=dict, kw_only=True)
  reference: dict[str, float] = field(default_factory=dict, kw_only=True)
  reference_wavelength: int | None = field(default=None, kw_only=True)


def _absorbance_od(
  calibration: _AbsorbancePrepareItem4Channel, item: _AbsorbanceRawItem4Channel, channel: int
) -> float:
  """i-control's OD for one channel of one read.

  OVER when no light is above dark (verified against the 230-1000 nm spectrum workbook) or
  the detector saturated; nan for too little reference signal or invalid calibration.
  """
  dark = calibration.measurement_dark[channel]
  bright = calibration.measurement_bright[channel]
  if bright <= dark or calibration.reference_bright <= calibration.reference_dark:
    return math.nan
  count = item.measurement_counts[channel]
  if count >= 65535 or item.reference_count >= 65535:
    return OVER
  reference = item.reference_count - calibration.reference_dark
  if reference < 500:
    return math.nan
  if count - dark <= 0:
    return OVER
  factor = (calibration.reference_bright - calibration.reference_dark) / (bright - dark)
  return -math.log10((count - dark) / reference * factor)


def _absorbance_item(block: bytes, wavelength: int) -> _AbsorbanceRawItem4Channel:
  packet = _decode_absorbance_data_4channel(block)
  if packet.number_of_packets is not None or len(packet.items) != 1:
    raise ValueError("Unsupported four-channel scan packet layout")
  if packet.ex_wavelength_raw != wavelength * 10:
    raise ValueError("Scan packet wavelength differs from the requested wavelength")
  return packet.items[0]


_CORRECTION_PARTS = ((0, 1000), (1000, 1000), (2000, 1000), (3000, 204))
"""``?CONFIG EXCORR|EMCORR <offset> <size>`` reads that make up one 3204-byte table."""

_EXSCAN_LABEL0_EXCITATION = 4850
"""Excitation (0.1 nm) i-control puts on label 0 of an excitation scan (captured)."""

_FLASH_SPEEDS: dict[int | str, int] = {400: 2500, 100: 10000, "on_the_fly": 10000}
"""``READS n,SPEED`` per flash mode (i-control: Mode 1 400 Hz, Mode 2 100 Hz, On-the-Fly)."""

_Z_SWEEP_POINTS = 38
"""Points of i-control's ``SCANZ`` sweep in the Z search."""

_OPTIMAL_GAIN_TARGET = 47500
"""Approximate count i-control's optimal gain aims for at 100 % (from a captured search)."""


def _parse_correction_table(data: bytes) -> list[float]:
  """801 little-endian float32 factors for 200-1000 nm in 1 nm steps."""
  if len(data) != 3204:
    raise ValueError("Correction table must be 3204 bytes")
  return [float(v) for v in struct.unpack("<801f", data)]


def _scan_correction(table: Sequence[float], wavelength: int, bandwidth: int) -> float:
  """i-control's ``DetermineFaktorMean``: mean of the table over the bandwidth (0.1 nm units)."""
  n = bandwidth // 10
  first = (wavelength - 2000) // 10 - n // 2
  window = table[first : first + n]
  if n < 1 or first < 0 or len(window) != n:
    raise ValueError("Wavelength or bandwidth outside the correction table")
  return sum(window) / n


def _calculate_optimal_gain(gain: int, measured: float, target: float) -> int:
  """i-control's ``CalculateOptimalGain``: signal scales with gain**6.8."""
  factor = min((target / max(measured, 1)) ** (1 / 6.8), 100.0)
  return int(gain * factor)


def _fluorescence_point(block: bytes, ex: int, em: int) -> tuple[int, int, int]:
  """Decode one 16-byte fluorescence point; return (label, count, reference)."""
  if len(block) != 16:
    raise ValueError("Invalid fluorescence scan packet length")
  label, block_ex, block_em, _, _, count, reference = struct.unpack(">HHHIHHH", block)
  if (label, block_ex, block_em) != (1, ex, em):
    raise ValueError("Unexpected fluorescence scan packet identity")
  return label, count, reference


def _on_the_fly_points(
  blocks: Sequence[bytes], ex: int, em: int, points: int
) -> list[tuple[int, int]]:
  """Decode an on-the-fly scan (data type 529): one packet with all points of a SCANX.

  Layout: label, ex, em, point count (4 x u16), then per point ticker (u32), counter,
  count and reference (3 x u16). Returns (count, reference) per point.
  """
  if len(blocks) != 1 or len(blocks[0]) < 8:
    raise ValueError("Invalid on-the-fly packet")
  label, block_ex, block_em, n = struct.unpack_from(">HHHH", blocks[0])
  if (label, block_ex, block_em) != (1, ex, em) or n != points or len(blocks[0]) != 8 + 10 * n:
    raise ValueError("Unexpected on-the-fly packet layout")
  return [(c, r) for _, _, c, r in struct.iter_unpack(">IHHH", blocks[0][8:])]


def _fluorescence_value(
  count: int, reference: int, dark: int, ref_dark: int, ref_bright: int
) -> float:
  """i-control's fluorescence value: dark-corrected count normalised to the reference."""
  if ref_bright <= ref_dark:
    return math.nan
  if count >= 65535 or reference >= 65535:
    return OVER
  if reference - ref_dark < 500:
    return math.nan
  return (count - dark) * (ref_bright - ref_dark) / (reference - ref_dark)


@dataclass
class InfiniteM1000ProFluorescenceResult(FluorescenceResult):
  """Fluorescence result with the gain used (the optimal-gain result when requested)."""

  gain: int | None = field(default=None, kw_only=True)
  z: int | None = field(default=None, kw_only=True)
  standard_deviation: dict[str, float] = field(default_factory=dict, kw_only=True)
  reads: dict[str, dict[str, float]] = field(default_factory=dict, kw_only=True)


def _encode(command: str, *, sequence: int = 0) -> bytes:
  """Frame an ASCII command using the reader's RDNet protocol."""
  payload = command.encode("ascii")
  if len(payload) > 65535 or any(byte in payload for byte in (2, 3, 13)):
    raise ValueError("Invalid command payload")
  if not 0 <= sequence <= 254:
    raise ValueError("Invalid command sequence")
  body = b"\x02" + payload + b"\x03"
  return (
    body + bytes([sequence]) + len(payload).to_bytes(2, "big") + bytes([reduce(xor, body, 0), 13])
  )


def _decode(frame: bytes, *, expected_sequence: int = 0) -> str:
  """Validate and decode one reader response for the current command sequence."""
  if len(frame) < 7 or frame[0] != 2 or frame[-1] != 13 or frame[-6] != 3:
    raise ValueError("Malformed reader response")
  if frame[-5] != expected_sequence:
    raise ValueError(f"Reader response sequence mismatch: {frame[-5]} != {expected_sequence}")
  if int.from_bytes(frame[-4:-2], "big") != len(frame) - 6:
    raise ValueError("Reader response length mismatch")
  if reduce(xor, frame[:-5], 0) != frame[-2]:
    raise ValueError("Reader response checksum mismatch")
  return frame[1:-6].decode("ascii")


class _ReaderStreamParser:
  """Incrementally decode framed text and announced binary blocks, without I/O."""

  def __init__(self, *, expected_sequence: int = 0) -> None:
    self._pending = bytearray()
    self._binary_size: int | None = None
    self._expected_sequence = expected_sequence

  def feed(self, chunk: bytes) -> list[str | bytes]:
    """Return complete events; retain incomplete frames for the next chunk."""
    self._pending.extend(chunk)
    events: list[str | bytes] = []
    while self._pending:
      if self._binary_size is not None:
        need = self._binary_size + 4
        if len(self._pending) < need:
          break
        block = bytes(self._pending[:need])
        del self._pending[:need]
        events.append(
          _decode_binary_block(block, self._binary_size, expected_sequence=self._expected_sequence)
        )
        self._binary_size = None
        continue
      if self._pending[0] != 2:
        raise ValueError("Unexpected reader stream prefix")
      end = self._pending.find(b"\x03", 1)
      if end >= 0 and end + 6 > 4096:
        raise ValueError("Reader response too long")
      if end < 0 or len(self._pending) < end + 6:
        if len(self._pending) > 4096:
          raise ValueError("Reader response too long")
        break
      frame = bytes(self._pending[: end + 6])
      del self._pending[: end + 6]
      text = _decode(frame, expected_sequence=self._expected_sequence)
      events.append(text)
      match = re.fullmatch(r"([0-9]+),BIN:", text)
      if match:
        size = int(match.group(1))
        if size > 1048576:
          raise ValueError("Binary block too large")
        self._binary_size = size
    return events


class InfiniteM1000Pro:
  """Control the tray of an Infinite M1000 Pro over USB."""

  def __init__(self) -> None:
    """Create a reader; setup must be called before operations."""
    self.usb = USB(
      id_vendor=0x0C47,
      id_product=0x8009,
      human_readable_device_name="Tecan Infinite M1000 Pro",
      read_endpoint_address=0x81,
      write_endpoint_address=0x02,
      packet_read_timeout=1,
      read_timeout=30,
      zero_length_packets=False,
    )
    self._buffer = bytearray()
    self._lock = asyncio.Lock()
    self._ready = False
    self._initialized = False
    self._startup_done = False
    self._next_sequence = 0
    self._active_sequence = 0
    self._correction: dict[str, list[float]] = {}

  async def setup(self) -> None:
    """Connect, initialize internal mechanisms and replay the i-control startup reads."""
    async with self._lock:
      if not self._ready:
        await self.usb.setup(empty_buffer=False)
        self._buffer.clear()
        self._next_sequence = 0
        self._active_sequence = 0
        self._ready = True
        logger.info("Connected to Tecan Infinite M1000 Pro")
      await self._initialize_locked()
      if not self._startup_done:
        await self._startup_reference_sequence()
        self._startup_done = True

  def _discard_pending(self) -> None:
    """Drop unexpected buffered input instead of refusing the next command."""
    if self._buffer:
      logger.warning("Discarding unexpected reader input: %s", bytes(self._buffer).hex())
      self._buffer.clear()

  async def stop(self) -> None:
    """Close the USB connection without moving the carrier."""
    async with self._lock:
      self._ready = False
      self._initialized = False
      self._startup_done = False
      self._buffer.clear()
      await self.usb.stop()
      logger.info("Disconnected from Tecan Infinite M1000 Pro")

  async def _write_command(self, command: str) -> None:
    """Write one command using the next RDNet sequence value."""
    self._active_sequence = self._next_sequence
    self._next_sequence = (self._next_sequence + 1) % 255
    logger.debug("Reader command [%d]: %s", self._active_sequence, command)
    await self.usb.write(_encode(command, sequence=self._active_sequence))

  async def _receive(self, timeout: float = 30) -> str:
    """Read one complete response, retaining any subsequent frame."""
    deadline = time.monotonic() + timeout
    while True:
      if time.monotonic() >= deadline:
        raise TimeoutError("Timed out waiting for reader response")
      if self._buffer and self._buffer[0] != 2:
        raise ValueError("Unexpected reader response prefix")
      end = self._buffer.find(b"\x03", 1)
      if end >= 0 and end + 6 > 4096:
        raise ValueError("Reader response too long")
      if end >= 0 and len(self._buffer) >= end + 6:
        frame = bytes(self._buffer[: end + 6])
        del self._buffer[: end + 6]
        response = _decode(frame, expected_sequence=self._active_sequence)
        if response.startswith(("MSG", "LOG:")):
          logger.info("Reader notification: %s", response)
          continue
        return response
      if len(self._buffer) > 4096:
        raise ValueError("Reader response too long")
      remaining = max(1, math.ceil(deadline - time.monotonic()))
      self._buffer.extend(await self.usb.read(timeout=remaining))

  async def initialize(self) -> None:
    """Initialize internal mechanisms (``INIT FORCE``) once per connection."""
    async with self._lock:
      await self._initialize_locked()

  async def _initialize_locked(self) -> None:
    """Initialize once while holding the reader's session lock."""
    if not self._ready:
      raise RuntimeError("Reader is not ready; call setup first")
    if self._initialized:
      return
    try:
      await self.usb.drain(timeout=0.1, max_duration=2)
      await self._write_command("QQ")
      status = await self._receive()
      if status != "ST":
        logger.warning("Reader not in standby before INIT FORCE: %r", status)
      self._discard_pending()
      await self._write_command("INIT FORCE")
      status = await self._receive()
      if not re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
        raise RuntimeError(f"Unexpected initialization acknowledgment: {status!r}")
      status = await self._receive(timeout=150)
      if status != "ST":
        raise RuntimeError(f"Unexpected initialization completion: {status!r}")
      self._discard_pending()
      self._initialized = True
      logger.info("Reader reported initialization complete")
    except BaseException:
      logger.exception("Initialization failed")
      raise

  async def _startup_reference_sequence(self) -> None:
    """Replay the capability and calibration reads observed during i-control startup.

    Responses are validated and discarded; they are not used as calibration inputs.
    This device-specific sequence is experimental and may change instrument modes.
    """
    optics = ("WAVELENGTH", "USAGE", "NAME", "BANDWIDTH", "ATTENUATION", "DESCRIPTION")
    commands = [
      "?CONFIG TEMPERATURE,INTERVAL",
      "#FUNCTIONS",
      "#MODULE ADDRESS",
      "?VERSION MAI",
      "?VERSION MEX",
      "?VERSION MEM",
      "?VERSION ABS",
      "?VERSION TCAN",
      "MODE FI.TOP",
      "?CONFIG CHECK,FLASHCOUNT",
      *(f"#EMISSION {item}" for item in optics),
      *(f"#EXCITATION {item}" for item in optics),
      "#TIME INTEGRATION",
      "#TIME LAG",
      "#TIME READDELAY",
      "#GAIN VALUE",
      "#BEAM DIAMETER",
      "#READS SPEED",
      "#READS NUMBER",
      "#POSITION FIL,Z",
      "MODE ABS",
      "?CONFIG CHECK,FLASHCOUNT",
      *(f"#EXCITATION {item}" for item in optics),
      "#TIME READDELAY",
      "#READS SPEED",
      "#READS NUMBER",
      "#BEAM DIAMETER",
      "#SHAKING MODE",
    ]
    for mode in ("ORBITAL", "LINEAR", "DOUBLE"):
      commands.extend(
        (f"SHAKING MODE={mode}", "#SHAKING AMPLITUDE", "#SHAKING FREQUENCY", "#SHAKING TIME")
      )
    commands.extend(
      (
        "#TEMPERATURE PLATE",
        "?CONFIG CHECK,HOURCOUNTER",
        "?CONFIG CHECK,FLASHCOUNT",
        "?VERSION MAI",
        "QQ",
        "#FUNCTIONS",
        "?VERSION",
        "#MEMORY MAI",
        "?MEMORY MAI,0,8",
        "#MEMORY MAI",
        "?MEMORY MAI,8,128",
        "?MEMORY MAI,136,31",
      )
    )
    for module in ("EXCORR", "EMCORR"):
      commands.extend(
        f"?CONFIG {module} {offset} {size}"
        for offset, size in ((0, 1000), (1000, 1000), (2000, 1000), (3000, 204))
      )
    for module in ("MEX", "MEM"):
      commands.extend(f"SIBUS {module} GSE 4 {index}" for index in range(6))
    tables: dict[str, list[bytes]] = {"EXCORR": [], "EMCORR": []}
    for command in commands:
      self._discard_pending()
      await self._write_command(command)
      if command.startswith(("?MEMORY ", "?CONFIG EXCORR ", "?CONFIG EMCORR ")):
        parser = _ReaderStreamParser(expected_sequence=self._active_sequence)
        events: list[str | bytes] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
          chunk = await self.usb.read(timeout=max(1, math.ceil(deadline - time.monotonic())))
          events.extend(parser.feed(chunk))
          if "ST" in events:
            if (
              len(events) != 3
              or not isinstance(events[0], str)
              or not isinstance(events[1], bytes)
              or events[2] != "ST"
              or events[0] != f"{len(events[1])},BIN:"
              or parser._pending
              or parser._binary_size is not None
            ):
              raise ValueError(f"Unexpected startup binary response to {command!r}")
            expected_length = int(re.split(r"[, ]", command)[-1])
            if len(events[1]) != expected_length:
              raise ValueError(f"Startup binary response length mismatch for {command!r}")
            if command.startswith("?CONFIG "):
              tables[command.split()[1]].append(events[1])
            break
        else:
          raise TimeoutError(f"Timed out waiting for {command!r}")
      else:
        response = await self._receive(timeout=60)
        if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", response):
          response = await self._receive(timeout=60)
        if command.startswith("MODE ") or command == "QQ":
          valid = response == "ST"
        elif command.startswith("SHAKING MODE="):
          valid = response == "+"
        else:
          valid = bool(response) and not response.startswith(("ER", "BY", "ST"))
        if not valid:
          logger.warning("Unexpected startup response to %r: %r", command, response)
        self._discard_pending()
    for kind, parts in tables.items():
      if len(parts) == len(_CORRECTION_PARTS):
        self._correction[kind] = _parse_correction_table(b"".join(parts))

  async def request_instrument_status(self) -> Literal["standby", "busy"]:
    """Query whether the reader reports standby or a busy state."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected")
      try:
        self._discard_pending()
        await self._write_command("QQ")
        status = await self._receive()
        self._discard_pending()
        if status == "ST":
          return "standby"
        if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", status):
          return "busy"
        raise RuntimeError(f"Unknown reader status: {status!r}")
      except BaseException:
        logger.exception("Reader status query failed")
        raise

  async def _position(self) -> str:
    """Query the carrier's reported logical position."""
    await self._write_command("?ABSOLUTE MTP,POS")
    return await self._receive()

  async def _log_position(self) -> str:
    """Query and log the carrier position without acting on it."""
    self._discard_pending()
    position = await self._position()
    if position != "IN":
      logger.warning("Carrier reports %r, not 'IN'; continuing", position)
    return position

  async def _move(self, target: Literal["IN", "OUT"]) -> None:
    """Move the carrier and report the instrument-reported position."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader is not ready; call setup first")
      try:
        position = await self._position()
        if position == target:
          return
        logger.info("Moving carrier to %s", target)
        await self._write_command(f"ABSOLUTE MTP,{target}")
        status = await self._receive()
        if re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
          status = await self._receive()
        if status != "ST":
          raise RuntimeError(f"Unexpected movement status: {status!r}")
        position = await self._position()
        if position != target:
          logger.warning("Carrier reports %r after moving to %r", position, target)
        else:
          logger.info("Reader reports carrier %s", target)
      except BaseException:
        logger.exception("Reader operation failed")
        raise

  async def _control_command(
    self, command: str, accepted: tuple[str, ...], *, timeout: float = 60
  ) -> None:
    """Send one configuration or positioning command and validate its response."""
    self._discard_pending()
    await self._write_command(command)
    response = await self._receive(timeout=timeout)
    if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", response):
      response = await self._receive(timeout=timeout)
    if response not in accepted or self._buffer:
      raise RuntimeError(f"Unexpected response to {command!r}: {response!r}")

  async def _temperature_query(self, field: Literal["CURRENT", "TARGET", "STATUS"]) -> str:
    """Read one temperature control field without changing state."""
    self._discard_pending()
    await self._write_command(f"?TEMPERATURE PLATE,{field}")
    response = await self._receive()
    self._discard_pending()
    return response

  async def request_current_temperature(self) -> float:
    """Read the current plate temperature in degrees Celsius."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      try:
        response = await self._temperature_query("CURRENT")
        if not re.fullmatch(r"-?[0-9]+", response):
          raise ValueError(f"Unexpected temperature response: {response!r}")
        return int(response) / 10
      except BaseException:
        logger.exception("Temperature query failed")
        raise

  async def set_temperature(self, temperature: float) -> None:
    """Set and enable plate temperature in °C (sent in tenths of a degree).

    No software range limit is applied; the firmware decides what it accepts.
    """
    if not math.isfinite(temperature):
      raise ValueError("Temperature must be a finite number")
    raw = round(temperature * 10)
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      try:
        target = await self._temperature_query("TARGET")
        status = await self._temperature_query("STATUS")
        if not re.fullmatch(r"-?[0-9]+", target) or status not in ("ON", "OFF"):
          raise RuntimeError("Unexpected temperature control state")
        if int(target) != raw:
          await self._control_command(f"TEMPERATURE PLATE,TARGET={raw}", ("ST", "+"))
          if await self._temperature_query("TARGET") != str(raw):
            raise RuntimeError("Temperature target was not confirmed")
        if status != "ON":
          await self._control_command("TEMPERATURE PLATE=ON", ("ST", "+"))
          if await self._temperature_query("STATUS") != "ON":
            raise RuntimeError("Temperature control was not confirmed ON")
      except BaseException:
        logger.exception("Temperature operation failed")
        raise

  async def stop_temperature_control(self) -> None:
    """Switch off plate temperature control if it is currently on."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      try:
        status = await self._temperature_query("STATUS")
        if status not in ("ON", "OFF"):
          raise RuntimeError("Unexpected temperature control state")
        if status == "ON":
          await self._control_command("TEMPERATURE PLATE=OFF", ("ST", "+"))
          if await self._temperature_query("STATUS") != "OFF":
            raise RuntimeError("Temperature control was not confirmed OFF")
      except BaseException:
        logger.exception("Temperature operation failed")
        raise

  async def _query(self, command: str) -> str:
    """Send one query and return its reply without interpreting it."""
    self._discard_pending()
    await self._write_command(command)
    response = await self._receive(timeout=60)
    self._discard_pending()
    return response

  async def shake(
    self,
    duration: int,
    *,
    mode: Literal["linear", "orbital", "double"] = "linear",
    amplitude: float = 2.0,
  ) -> None:
    """Shake the plate for ``duration`` whole seconds and wait until shaking stops.

    ``amplitude`` is in mm. The reader has one fixed frequency per mode and amplitude
    (see ``SHAKING_FREQUENCIES``); ``SHAKING TIME`` is always sent because the reader
    keeps the previous duration otherwise.
    """
    frequencies = SHAKING_FREQUENCIES[mode]
    amplitude_um = round(amplitude * 1000)
    if amplitude_um not in frequencies:
      raise ValueError(
        f"{mode} shaking supports amplitudes {[a / 1000 for a in frequencies]} mm, not {amplitude}"
      )
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
      raise ValueError("Shaking duration must be a positive whole number of seconds")
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      try:
        await self._log_position()
        for command in (
          f"SHAKING MODE={mode.upper()}",
          f"SHAKING TIME={duration}",
          f"SHAKING AMPLITUDE={amplitude_um}",
          f"SHAKING FREQUENCY={frequencies[amplitude_um]}",
        ):
          await self._control_command(command, ("+",))
        for command in (
          "?STEPABS MTP,X",
          "?STEPABS MTP,Y",
          "?CONFIG OFFSET,MTP.HEAT.X",
          "?CONFIG OFFSET,MTP.HEAT.Y",
        ):
          logger.debug("%s -> %r", command, await self._query(command))
        await self._control_command("STEPABS MTP,Y=0", ("ST",))
        await self._control_command("SHAKING ON", ("ST",), timeout=duration + 60)
        await self._control_command("STEPABS MTP,Y=0", ("ST",))
      except BaseException:
        logger.exception("Shaking failed")
        raise

  async def _absorbance_burst(
    self, command: str, expected_type: int, *, max_blocks: int = 64, timeout: float = 90
  ) -> list[bytes]:
    """Receive a complete bounded binary measurement burst."""
    self._discard_pending()
    await self._write_command(command)
    parser = _ReaderStreamParser(expected_sequence=self._active_sequence)
    events: list[str | bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      chunk = await self.usb.read(timeout=max(1, math.ceil(deadline - time.monotonic())))
      logger.debug("Reader binary input: %s", bytes(chunk).hex())
      events.extend(parser.feed(chunk))
      if len(events) > 2 * max_blocks + 8:
        raise ValueError("Too many binary burst events")
      if "ST" in events:
        if events[-1] != "ST" or parser._pending or parser._binary_size is not None:
          raise ValueError("Unexpected data after binary burst")
        return _validate_absorbance_burst(events, expected_type=expected_type)
    raise TimeoutError("Timed out waiting for absorbance burst")

  async def _configure_absorbance(
    self,
    excitation: str,
    *,
    settle_time: int,
    flashes: int,
    z: int,
    direction: str,
    beam_diameter: bool,
    reference: str | None = None,
    start_run: bool = True,
  ) -> tuple[float | None, int | None]:
    """Send i-control's absorbance setup; return plate temperature and lamp flash counter.

    With ``reference`` (a dual-wavelength read) i-control first puts the reference
    wavelength on label 0 and then overwrites it with the measurement wavelength.
    ``start_run=False`` skips the temperature/MODE/flash-counter preamble (second pass).
    """
    temperature = flashcount = None
    if start_run:
      temperature_raw = await self._query("?TEMPERATURE PLATE,CURRENT")
      if re.fullmatch(r"-?[0-9]+", temperature_raw):
        temperature = int(temperature_raw) / 10
      await self._control_command("MODE ABS", ("ST",))
      flashcount_raw = await self._query("?CONFIG CHECK,FLASHCOUNT")
      flashcount = int(flashcount_raw) if flashcount_raw.isdigit() else None
      if flashcount is None:
        logger.warning("Unexpected flash counter reply %r; it will not be updated", flashcount_raw)
    await self._control_command("EXCITATION CLEAR", ("+",))
    await self._query("#EXCITATION MAXINDEX")
    for command in ("TIME CLEAR", "GAIN CLEAR", "READS CLEAR", "POSITION CLEAR"):
      await self._control_command(command, ("+",))
    await self._control_command("FLASH ENERGY=LOW", ("ST",))
    for label in (0, 1):
      first = reference if label == 0 and reference else excitation
      await self._control_command(f"EXCITATION {label},ABS,{first}", ("+",))
      await self._control_command(f"TIME {label},READDELAY={settle_time * 1000}", ("+",))
      await self._control_command(f"READS {label},NUMBER={flashes}", ("+",))
      await self._control_command(f"READS {label},SPEED=2500", ("+",))
      if label == 0:
        await self._query("#POSITION ABS,Z")
      await self._control_command(f"POSITION {label},Z={z}", ("ST",))
      if label == 0:
        await self._control_command(f"SCAN DIRECTION={direction}", ("+",))
        await self._control_command("RATIO LABELS=1", ("+",))
        if beam_diameter:
          await self._control_command("BEAM DIAMETER=800", ("ST",))
        if reference:
          await self._control_command(f"EXCITATION 0,ABS,{excitation}", ("+",))
    return temperature, flashcount

  async def _prepare_absorbance_reference(
    self, wavelengths: Sequence[int], *, dump_runtime: bool = False, position_query: bool = True
  ) -> dict[int, _AbsorbancePrepareItem4Channel]:
    """Run ``PREPARE REF``; return one calibration item per raw wavelength (0.1 nm)."""
    blocks = await self._absorbance_burst(
      "PREPARE REF", 1024, max_blocks=len(wavelengths), timeout=60 + 0.5 * len(wavelengths)
    )
    calibration: dict[int, _AbsorbancePrepareItem4Channel] = {}
    for block in blocks:
      packet = _decode_absorbance_prepare_4channel(block)
      if len(packet.items) != 1:
        raise ValueError("Unexpected absorbance calibration layout")
      calibration[packet.ex_wavelength_raw] = packet.items[0]
    if sorted(calibration) != sorted(w * 10 for w in wavelengths) or len(blocks) != len(
      wavelengths
    ):
      raise ValueError("Calibration wavelengths differ from the requested wavelengths")
    if dump_runtime:  # i-control reads the runtime dump in dual-wavelength reads
      await self._query("#DUMP RUNTIME,SIZE")
      await self._query("#DUMP RUNTIME,TIMEOUT")
      self._discard_pending()
      await self._write_command("?DUMP RUNTIME")
      events = await self._read_burst_events(30)
      logger.debug("Runtime dump: %r", events)
    if position_query:
      await self._query("#POSITION MTP Y")
    return calibration

  async def _scan(self, command: str, points: int, wavelengths: int) -> list[bytes]:
    """Run one ``SCANX`` and check it returned one block per point and wavelength."""
    count = points * wavelengths
    blocks = await self._absorbance_burst(command, 1040, max_blocks=count, timeout=60 + 0.5 * count)
    if len(blocks) != count:
      raise ValueError(f"Expected {count} scan blocks, got {len(blocks)}")
    return blocks

  async def _finish_measurement(self, flashcount: int | None, scans: int) -> None:
    """End a run as i-control does: TERMINATE, lamp flash counter update, TCAN SSL 0."""
    await self._control_command("TERMINATE", ("ST",))
    if flashcount is not None:
      await self._control_command(f"CONFIG CHECK,FLASHCOUNT={flashcount + scans}", ("ST",))
    await self._control_command("SIBUS TCAN SSL 0", ("ST",))

  async def _abort_measurement(self, flashcount: int | None, scans: int) -> None:
    """Stop a running measurement as i-control's Stop button does.

    i-control sends ``TT`` (reply ``TT#T<ms>`` then ``ST``) while data may still be
    streaming, then ``QQ``, the lamp flash counter and ``SIBUS TCAN SSL 0``; no ``TERMINATE``.
    """
    try:
      self._buffer.clear()
      await self._write_command("TT")
      marker = b"\x02ST\x03" + bytes([self._active_sequence])
      received = bytearray()
      deadline = time.monotonic() + 70
      while marker not in received:
        if time.monotonic() > deadline:
          raise TimeoutError("No ST after TT")
        try:
          received.extend(await self.usb.read(timeout=5))
        except TimeoutError:
          continue
      self._buffer.clear()
      await self._control_command("QQ", ("ST",))
      if flashcount is not None:
        await self._control_command(f"CONFIG CHECK,FLASHCOUNT={flashcount + scans}", ("ST",))
      await self._control_command("SIBUS TCAN SSL 0", ("ST",))
      logger.warning("Measurement aborted with TT")
    except Exception:
      logger.exception("Aborting the measurement failed")

  async def read_absorbance(
    self,
    plate: Plate,
    wells: Sequence[Well] | None = None,
    wavelength: int = 600,
    *,
    plate_definition: str | PlateGeometry,
    plate_lid: bool = False,
    flashes: int = 25,
    settle_time: int = 0,
    z: int | None = None,
    reads_per_well: int = 1,
    multi_read_pattern: MultiReadPattern = "filled_square",
    multi_read_border: int = 500,
    reference_wavelength: int | None = None,
  ) -> InfiniteM1000ProAbsorbanceResult:
    """Read absorbance at one wavelength.

    Args:
      plate: the plate in the reader; its grid must match ``plate_definition``.
      wells: wells to report; None reads every well. The four channels read four rows at
        once and each scan covers the selected columns' range, so neighbouring wells may be
        measured but are not reported.
      wavelength: nm (230-1000); bandwidth is 2.5 nm up to 300 nm and 5 nm above.
      plate_definition: i-control plate definition name (a ``.pdfx`` in ``PDFX_DIRECTORY``)
        or a ``PlateGeometry``.
      plate_lid: the plate has a lid; Z uses the definition's height with cover.
      flashes: flashes per read (``READS NUMBER``).
      settle_time: ms to wait before each read (``READDELAY``, sent in µs).
      z: measurement height in µm; None computes i-control's Z from the plate definition.
      reads_per_well: size N of a multiple-reads pattern (1 = single read); values are
        averaged and ``standard_deviation`` / ``reads`` hold the detail, keyed by i-control's
        ``"x;y"`` labels (any OVER read makes the well nan, i-control's ``INVALID``).
        i-control requires a settle time of 10-100 ms with multiple reads.
      multi_read_pattern: ``filled_square`` (N x N), ``square`` (border of N x N), ``x_line``,
        ``y_line`` or ``xy_lines`` (cross); spacing follows i-control from the well size.
      multi_read_border: µm kept free from the well edge.
      reference_wavelength: nm; i-control's dual-wavelength read. Both wavelengths are read
        in separate passes and ``data`` is OD(wavelength) - OD(reference); ``measurement``
        and ``reference`` hold the two ODs. Not combined with multiple reads.

    Saturated wells are reported as ``OVER`` (``math.inf``), wells with too little reference
    signal as ``nan``.
    """
    geometry = _resolve_geometry(plate_definition)
    cells = _selected_cells(plate, wells, geometry)
    z = geometry.absorbance_z(plate_lid) if z is None else z
    _check_reads(flashes, settle_time)
    if not isinstance(reads_per_well, int) or reads_per_well < 1:
      raise ValueError("reads_per_well must be a positive integer")
    multi = reads_per_well > 1
    if multi:
      runs, step_x, step_y = _multi_read_runs(
        geometry, multi_read_pattern, reads_per_well, multi_read_border, _ABSORBANCE_BEAM_DIAMETER
      )
      if not 10 <= settle_time <= 100:
        logger.warning("i-control requires a 10-100 ms settle time with multiple reads")
    if reference_wavelength is not None and multi:
      raise ValueError("reference_wavelength cannot be combined with multiple reads")
    passes = _plan_passes(geometry, cells, channels=4)
    bandwidth = 25 if wavelength <= 300 else 50

    def excitation_of(nm: int) -> str:
      return f"{nm * 10},{25 if nm <= 300 else 50},0"

    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      start = datetime.now()
      started = False
      flashcount: int | None = None
      scans = 0
      try:
        await self._log_position()
        temperature, flashcount = await self._configure_absorbance(
          f"{wavelength * 10},{bandwidth},0",
          settle_time=settle_time,
          flashes=flashes,
          z=z,
          direction="ALTUP",
          beam_diameter=True,
          reference=None if reference_wavelength is None else excitation_of(reference_wavelength),
        )
        started = True
        dual = reference_wavelength is not None
        calibration = await self._prepare_absorbance_reference([wavelength], dump_runtime=dual)
        reads: dict[tuple[int, int], dict[str, float]] = {}
        n = reads_per_well
        half = (n - 1) / 2
        for pass_index, scan_pass in enumerate(passes):
          xs = [geometry.x(column) for column in scan_pass.columns]
          if not multi:
            await self._control_command(f"ABSOLUTE MTP,Y={scan_pass.y}", ("ST",))
            await self._control_command(f"ABSOLUTE MTP,X={xs[0]},Y={scan_pass.y}", ("ST",))
            scans += 1
            blocks = await self._scan(f"SCANX {xs[0]},{xs[-1]},{len(xs)}", len(xs), 1)
            for column, block in zip(scan_pass.columns, blocks):
              item = _absorbance_item(block, wavelength)
              for channel, row in enumerate(scan_pass.rows):
                if row is not None and (row, column) in cells:
                  od = _absorbance_od(calibration[wavelength * 10], item, channel)
                  reads[(row, column)] = {"": od}
            continue
          for column_index, (column, x) in enumerate(zip(scan_pass.columns, xs)):
            if column_index == 0:
              await self._control_command(f"ABSOLUTE MTP,X={x},Y={scan_pass.y}", ("ST",))
              if pass_index == 0:
                await self._control_command("MOVEMENT MTP,ACCELERATION=LOW", ("ST",))
            for run in runs:
              y = _round_half_up(scan_pass.y + (run[0][1] - half) * step_y)
              run_xs = [_round_half_up(x + (i - half) * step_x) for i, _ in run]
              await self._control_command(f"ABSOLUTE MTP,X={run_xs[0]},Y={y}", ("ST",))
              scans += 1
              blocks = await self._scan(f"SCANX {run_xs[0]},{run_xs[-1]},{len(run)}", len(run), 1)
              for (i, j), block in zip(run, blocks):
                item = _absorbance_item(block, wavelength)
                label = f"{i};{n - 1 - j}"
                for channel, row in enumerate(scan_pass.rows):
                  if row is not None and (row, column) in cells:
                    od = _absorbance_od(calibration[wavelength * 10], item, channel)
                    reads.setdefault((row, column), {})[label] = od
        if multi:
          await self._control_command("MOVEMENT MTP,ACCELERATION=NORMAL", ("ST",))
        reference_reads: dict[tuple[int, int], float] = {}
        if reference_wavelength is not None:
          # second pass at the reference wavelength, as i-control's dual-wavelength read
          await self._query("#POSITION MTP Y")
          await self._control_command(f"ABSOLUTE MTP,Y={passes[0].y}", ("ST",))
          await self._configure_absorbance(
            excitation_of(reference_wavelength),
            settle_time=settle_time,
            flashes=flashes,
            z=z,
            direction="ALTUP",
            beam_diameter=True,
            start_run=False,
          )
          ref_cal = await self._prepare_absorbance_reference(
            [reference_wavelength], dump_runtime=True, position_query=False
          )
          for scan_pass in passes:
            xs = [geometry.x(column) for column in scan_pass.columns]
            for _ in range(2):
              await self._control_command(f"ABSOLUTE MTP,X={xs[0]},Y={scan_pass.y}", ("ST",))
            scans += 1
            blocks = await self._scan(f"SCANX {xs[0]},{xs[-1]},{len(xs)}", len(xs), 1)
            for column, block in zip(scan_pass.columns, blocks):
              item = _absorbance_item(block, reference_wavelength)
              for channel, row in enumerate(scan_pass.rows):
                if row is not None and (row, column) in cells:
                  reference_reads[(row, column)] = _absorbance_od(
                    ref_cal[reference_wavelength * 10], item, channel
                  )
        await self._finish_measurement(flashcount, scans)
      except BaseException:
        logger.exception("Absorbance operation failed")
        if started:
          await self._abort_measurement(flashcount, scans)
        raise
    data: dict[str, float] = {}
    deviation: dict[str, float] = {}
    detail: dict[str, dict[str, float]] = {}
    measured: dict[str, float] = {}
    referenced: dict[str, float] = {}
    for (row, column), values in sorted(reads.items()):
      well = plate.get_item((row, column)).get_identifier()
      points = list(values.values())
      if reference_reads:
        measured[well] = points[0]
        referenced[well] = reference_reads[(row, column)]
        data[well] = points[0] - referenced[well]
        continue
      if not multi:
        data[well] = points[0]
        continue
      detail[well] = values
      if all(math.isfinite(v) for v in points):
        data[well] = statistics.mean(points)
        deviation[well] = statistics.stdev(points)
      else:
        data[well] = deviation[well] = math.nan
    return InfiniteM1000ProAbsorbanceResult(
      data=data,
      wavelength=wavelength,
      temperature=temperature,
      start_timestamp=start,
      end_timestamp=datetime.now(),
      standard_deviation=deviation,
      reads=detail,
      measurement=measured,
      reference=referenced,
      reference_wavelength=reference_wavelength,
    )

  async def read_absorbance_spectrum(
    self,
    plate: Plate,
    wells: Sequence[Well] | None = None,
    *,
    start_wavelength: int,
    end_wavelength: int,
    step_size: int,
    plate_definition: str | PlateGeometry,
    plate_lid: bool = False,
    flashes: int = 25,
    settle_time: int = 0,
    z: int | None = None,
  ) -> AbsorbanceSpectrumResult:
    """Scan absorbance from ``start_wavelength`` to ``end_wavelength`` nm, well by well.

    i-control reads each column position separately (about 47 s per position for 386
    wavelengths). Bandwidth is 2.5 nm when the scan starts at or below 300 nm, else 5 nm.
    ``OVER`` (``math.inf``) marks wavelengths where no light passed the sample.
    """
    if step_size < 1 or end_wavelength <= start_wavelength:
      raise ValueError("end_wavelength must exceed start_wavelength and step_size must be >= 1")
    if (end_wavelength - start_wavelength) % step_size:
      raise ValueError("step_size must divide the wavelength range")
    wavelengths = list(range(start_wavelength, end_wavelength + 1, step_size))
    geometry = _resolve_geometry(plate_definition)
    cells = _selected_cells(plate, wells, geometry)
    z = geometry.absorbance_z(plate_lid) if z is None else z
    _check_reads(flashes, settle_time)
    passes = _plan_passes(geometry, cells, channels=4)
    bandwidth = 25 if start_wavelength <= 300 else 50
    excitation = f"{start_wavelength * 10}~{end_wavelength * 10}:{step_size * 10},{bandwidth},0"
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      start = datetime.now()
      started = False
      flashcount: int | None = None
      scans = 0
      try:
        await self._log_position()
        temperature, flashcount = await self._configure_absorbance(
          excitation,
          settle_time=settle_time,
          flashes=flashes,
          z=z,
          direction="UP",
          beam_diameter=False,
        )
        started = True
        calibration = await self._prepare_absorbance_reference(wavelengths)
        spectra: dict[tuple[int, int], dict[int, float]] = {}
        for scan_pass in passes:
          xs = [geometry.x(column) for column in scan_pass.columns]
          await self._control_command(f"ABSOLUTE MTP,X={xs[0]},Y={scan_pass.y}", ("ST",))
          for column, x in zip(scan_pass.columns, xs):
            await self._control_command(f"ABSOLUTE MTP,X={x},Y={scan_pass.y}", ("ST",))
            scans += 1
            blocks = await self._scan(f"SCANX {x},{x},1", 1, len(wavelengths))
            for block in blocks:
              packet = _decode_absorbance_data_4channel(block)
              if len(packet.items) != 1 or packet.ex_wavelength_raw not in calibration:
                raise ValueError("Unexpected spectrum scan packet")
              for channel, row in enumerate(scan_pass.rows):
                if row is not None and (row, column) in cells:
                  spectra.setdefault((row, column), {})[packet.ex_wavelength_raw // 10] = (
                    _absorbance_od(calibration[packet.ex_wavelength_raw], packet.items[0], channel)
                  )
        await self._finish_measurement(flashcount, scans)
      except BaseException:
        logger.exception("Absorbance spectrum failed")
        if started:
          await self._abort_measurement(flashcount, scans)
        raise
    data = {
      plate.get_item(cell).get_identifier(): dict(sorted(spectrum.items()))
      for cell, spectrum in sorted(spectra.items())
    }
    return AbsorbanceSpectrumResult(
      data=data, temperature=temperature, start_timestamp=start, end_timestamp=datetime.now()
    )

  async def _correction_table(self, kind: Literal["EXCORR", "EMCORR"]) -> list[float]:
    """The reader's excitation/emission scan correction table (801 values, 200-1000 nm)."""
    if kind not in self._correction:
      parts = []
      for offset, size in _CORRECTION_PARTS:
        self._discard_pending()
        await self._write_command(f"?CONFIG {kind} {offset} {size}")
        events = await self._read_burst_events(60)
        if len(events) != 3 or not isinstance(events[1], bytes) or len(events[1]) != size:
          raise ValueError(f"Unexpected {kind} response")
        parts.append(events[1])
      self._correction[kind] = _parse_correction_table(b"".join(parts))
    return self._correction[kind]

  async def _read_burst_events(self, timeout: float) -> list[str | bytes]:
    """Read text/binary events for the active command until its ``ST``."""
    parser = _ReaderStreamParser(expected_sequence=self._active_sequence)
    events: list[str | bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      chunk = await self.usb.read(timeout=max(1, math.ceil(deadline - time.monotonic())))
      events.extend(parser.feed(chunk))
      if "ST" in events:
        return events
    raise TimeoutError("Timed out waiting for reader data")

  async def _configure_fluorescence(
    self,
    labels: Sequence[tuple[str, str]],
    *,
    gain: int,
    flashes: int,
    flash_frequency: int | str,
    integration_time: int,
    lag_time: int,
    settle_time: int,
    z: int | None,
    beam_diameter: bool,
    query_temperature: bool = True,
  ) -> tuple[float | None, int | None]:
    """Send i-control's top-fluorescence setup; return plate temperature and flash counter.

    ``z=None`` leaves out the ``POSITION`` commands (i-control does so before a Z search).
    ``flash_frequency="on_the_fly"`` reads one high-energy flash per well while moving.
    """
    temperature = None
    if query_temperature:
      temperature_raw = await self._query("?TEMPERATURE PLATE,CURRENT")
      if re.fullmatch(r"-?[0-9]+", temperature_raw):
        temperature = int(temperature_raw) / 10
    await self._control_command("MODE FI.TOP", ("ST",))
    flashcount_raw = await self._query("?CONFIG CHECK,FLASHCOUNT")
    flashcount = int(flashcount_raw) if flashcount_raw.isdigit() else None
    await self._control_command("EMISSION CLEAR", ("+",))
    await self._query("#EMISSION MAXINDEX")
    await self._control_command("EXCITATION CLEAR", ("+",))
    await self._query("#EXCITATION MAXINDEX")
    for command in ("TIME CLEAR", "GAIN CLEAR", "READS CLEAR", "POSITION CLEAR"):
      await self._control_command(command, ("+",))
    on_the_fly = flash_frequency == "on_the_fly"
    await self._control_command(f"FLASH ENERGY={'HIGH' if on_the_fly else 'LOW'}", ("ST",))
    speed = _FLASH_SPEEDS[flash_frequency]
    for label, (excitation, emission) in enumerate(labels):
      for command in (
        f"EXCITATION {label},FI,{excitation}",
        f"EMISSION {label},FI,{emission}",
        f"TIME {label},INTEGRATION={integration_time}",
        f"TIME {label},LAG={lag_time}",
        f"TIME {label},READDELAY={settle_time * 1000}",
        f"GAIN {label},VALUE={gain}",
        f"READS {label},NUMBER={1 if on_the_fly else flashes}",
        f"READS {label},SPEED={speed}",
      ):
        await self._control_command(command, ("+",))
      if z is not None:
        await self._control_command(f"POSITION {label},Z={z}", ("ST",))
      if label == 0:
        await self._control_command("SCAN DIRECTION=UP", ("+",))
        await self._control_command("RATIO LABELS=1", ("+",))
        if beam_diameter:
          await self._control_command("BEAM DIAMETER=2500", ("ST",))
    return temperature, flashcount

  async def _prepare_fluorescence_reference(
    self, excitation: int, gain: int
  ) -> tuple[int, int, int]:
    """Run ``PREPARE REF``; return (dark, reference dark, reference bright) counts."""
    blocks = await self._absorbance_burst("PREPARE REF", 512)
    if len(blocks) != 1 or len(blocks[0]) != 18:
      raise ValueError("Invalid fluorescence calibration packet")
    cal_ex, _, _, cal_gain, dark, _, ref_dark, ref_bright = struct.unpack(">HI6H", blocks[0])
    if cal_ex != excitation or cal_gain != gain:
      raise ValueError("Fluorescence calibration differs from the requested settings")
    return dark, ref_dark, ref_bright

  async def _single_point(self, command: str, ex: int, em: int, points: int) -> list[int]:
    """Run a SCANX/SCANZ and return the counts of its 16-byte fluorescence points."""
    blocks = await self._absorbance_burst(command, 528, max_blocks=points)
    if len(blocks) != points:
      raise ValueError(f"Expected {points} points from {command!r}, got {len(blocks)}")
    return [_fluorescence_point(block, ex, em)[1] for block in blocks]

  async def _converge_gain(self, gain: int, count: int, target: float, measure) -> int:
    """Refine the gain with i-control's gain**6.8 model inside a shrinking bracket."""
    low, high = 0, 256  # gains known to be below / above the target
    for _ in range(16):
      if abs(count - target) <= 0.05 * target:
        break
      if count < target:
        low = max(low, gain)
      else:
        high = min(high, gain)
      new = _calculate_optimal_gain(gain, count, target)
      if not low < new < high:
        new = (low + high) // 2
      new = max(1, min(255, new))
      if new == gain:
        break
      gain = new
      await self._control_command(f"GAIN 1,VALUE={gain}", ("+",))
      count = await measure()
    return gain

  async def _optimal_gain(
    self,
    geometry: PlateGeometry,
    passes: Sequence[_ScanPass],
    cells: set[tuple[int, int]],
    label: tuple[str, str],
    percentage: int,
    settings: dict[str, Any],
    gain_cell: tuple[int, int] | None = None,
  ) -> int:
    """i-control-style gain search (3 flashes, PREPARE NOREF, gain**6.8 model).

    "Optimal": raise the gain on the first well, then refine it on the brightest selected
    well. "Calculated from well" (``gain_cell``): both steps on that well. The target count
    and stop rule approximate i-control's; the gain can differ from i-control's by a few.
    """
    gain = 50
    target = _OPTIMAL_GAIN_TARGET * percentage / 100
    await self._configure_fluorescence(
      [label, label], gain=gain, query_temperature=False, **settings
    )
    await self._control_command("READS 0,NUMBER=3", ("+",))
    await self._control_command("READS 1,NUMBER=3", ("+",))
    await self._query("?FLASH ENERGY")
    ex = int(label[0].split(",")[0])
    em = int(label[1].split(",")[0])

    async def measure(x: int, y: int) -> int:
      await self._control_command(f"ABSOLUTE MTP,X={x},Y={y}", ("ST",))
      await self._control_command("PREPARE NOREF", ("ST",))
      await self._control_command(f"ABSOLUTE MTP,X={x},Y={y}", ("ST",))
      return (await self._single_point(f"SCANX {x},{x},1", ex, em, 1))[0]

    if gain_cell is not None:
      ordered = [(geometry.x(gain_cell[1]), geometry.y(gain_cell[0]))]
    else:
      ordered = [
        (geometry.x(column), scan_pass.y)
        for scan_pass in passes
        for column in scan_pass.columns
        if scan_pass.rows[0] is not None and (scan_pass.rows[0], column) in cells
      ]
    first = ordered[0]
    gain = await self._converge_gain(gain, await measure(*first), target, lambda: measure(*first))
    if gain_cell is None:
      counts = [(await measure(x, y), (x, y)) for x, y in ordered]
      brightest_count, brightest = max(counts)
    else:
      brightest = first
      brightest_count = await measure(*first)
    gain = await self._converge_gain(gain, brightest_count, target, lambda: measure(*brightest))
    logger.info("Optimal gain %d (target %.0f counts)", gain, target)
    return gain

  async def _optimal_z(
    self,
    geometry: PlateGeometry,
    cell: tuple[int, int],
    label: tuple[str, str],
    gain: int,
    settings: dict[str, Any],
  ) -> int:
    """i-control's "Z calculated from well": Z sweeps with SCANZ, gain fixed on the peak,
    then the brightest Z of a final upward sweep (verified against a captured run).

    The sweep spans the plate's usable height (height + tolerance) to the Z maximum in 38
    points; the gain steps use the same approximate target as the optimal gain.
    """
    x, y = geometry.x(cell[1]), geometry.y(cell[0])
    low, high = geometry.height + geometry.height_tolerance, _Z_MAX
    target = _OPTIMAL_GAIN_TARGET
    flashes = settings["flashes"]
    speed = _FLASH_SPEEDS[settings["flash_frequency"]]
    ex = int(label[0].split(",")[0])
    em = int(label[1].split(",")[0])
    no_z = {**settings, "z": None}
    await self._configure_fluorescence([label, label], gain=gain, query_temperature=False, **no_z)
    await self._control_command("MODE FI.TOP", ("ST",))
    await self._query("?CONFIG CHECK,FLASHCOUNT")
    await self._control_command(f"ABSOLUTE MTP,X={x},Y={y}", ("ST",))
    await self._query("#POSITION FIL,Z")
    await self._control_command("RATIO LABELS=1", ("+",))
    await self._control_command("READS 0,NUMBER=3", ("+",))
    await self._control_command(f"POSITION 0,Z={high}", ("ST",))
    for command in (
      f"EXCITATION 0,FI,{label[0]}",
      f"EMISSION 0,FI,{label[1]}",
      f"EXCITATION 1,FI,{label[0]}",
      f"EMISSION 1,FI,{label[1]}",
      f"TIME 1,INTEGRATION={settings['integration_time']}",
      f"TIME 1,LAG={settings['lag_time']}",
      f"TIME 1,READDELAY={settings['settle_time'] * 1000}",
      f"GAIN 1,VALUE={gain}",
      f"READS 1,NUMBER={flashes}",
      f"READS 1,SPEED={speed}",
      "READS 1,NUMBER=3",
    ):
      await self._control_command(command, ("+",))
    await self._control_command(f"POSITION 1,Z={high}", ("ST",))

    async def set_z(z: int) -> None:
      await self._control_command(f"POSITION 0,Z={z}", ("ST",))
      await self._control_command(f"POSITION 1,Z={z}", ("ST",))

    async def sweep(start: int, end: int) -> list[tuple[int, int]]:
      await self._control_command("PREPARE NOREF", ("ST",))
      await self._control_command(f"ABSOLUTE MTP,X={x},Y={y}", ("ST",))
      counts = await self._single_point(
        f"SCANZ {start},{end},{_Z_SWEEP_POINTS}", ex, em, _Z_SWEEP_POINTS
      )
      zs = [start + int(k * (end - start) / (_Z_SWEEP_POINTS - 1)) for k in range(len(counts))]
      return list(zip(zs, counts))

    start, end = high, low
    points = await sweep(start, end)
    for _ in range(8):
      peak = max(count for _, count in points)
      if peak >= 0.5 * target or gain >= 255:
        break
      gain = max(gain + 1, min(255, _calculate_optimal_gain(gain, peak, target)))
      await self._control_command(f"GAIN 1,VALUE={gain}", ("+",))
      start, end = end, start
      await set_z(start)
      points = await sweep(start, end)
    peak_z, peak = max(points, key=lambda point: point[1])
    new = max(1, min(255, _calculate_optimal_gain(gain, peak, target)))
    if new != gain:
      gain = new
      await self._control_command(f"GAIN 1,VALUE={gain}", ("+",))
    await set_z(peak_z)

    async def single() -> int:
      await self._control_command("PREPARE NOREF", ("ST",))
      await self._control_command(f"ABSOLUTE MTP,X={x},Y={y}", ("ST",))
      return (await self._single_point(f"SCANZ {peak_z},{peak_z},1", ex, em, 1))[0]

    gain = await self._converge_gain(gain, await single(), target, single)
    await set_z(low)
    final = await sweep(low, high)
    best_z = max(final, key=lambda point: point[1])[0]
    logger.info("Optimal Z %d (search gain %d)", best_z, gain)
    return best_z

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: Sequence[Well] | None = None,
    excitation_wavelength: float = 485,
    emission_wavelength: float = 535,
    *,
    plate_definition: str | PlateGeometry,
    excitation_bandwidth: float = 5,
    emission_bandwidth: float = 5,
    gain: int | Literal["optimal"] = 100,
    optimal_gain_percentage: int = 100,
    gain_well: Well | None = None,
    flashes: int = 50,
    flash_frequency: Literal[400, 100, "on_the_fly"] = 400,
    integration_time: int = 20,
    lag_time: int = 0,
    settle_time: int = 0,
    z: int | Literal["optimal"] = 20000,
    z_well: Well | None = None,
    reads_per_well: int = 1,
    multi_read_pattern: MultiReadPattern = "filled_square",
    multi_read_border: int = 500,
  ) -> "InfiniteM1000ProFluorescenceResult":
    """Read top fluorescence intensity.

    Args:
      plate: the plate in the reader; its grid must match ``plate_definition``.
      wells: wells to report; None reads every well. Each plate row is one scan over the
        selected columns' range.
      excitation_wavelength / emission_wavelength: nm, in 0.1 nm steps (230-850 / 280-850).
      excitation_bandwidth / emission_bandwidth: nm (excitation 2.5-10 up to 300 nm,
        5-20 above; emission 5-20).
      gain: manual detector gain (0-255), or ``"optimal"``: search the gain that puts the
        brightest selected well (or ``gain_well``, i-control's "Calculated from well") at
        ``optimal_gain_percentage`` of the target signal.
      flashes: flashes per well (``READS NUMBER``); ignored for ``"on_the_fly"``.
      flash_frequency: 400 or 100 Hz (i-control's Mode 1 / Mode 2), or ``"on_the_fly"``
        (one high-energy flash per well while the plate moves).
      integration_time / lag_time: µs.
      settle_time: ms before each read (``READDELAY``, sent in µs).
      z: measurement height in µm, or ``"optimal"``: i-control's "Calculated from well"
        Z search on ``z_well`` (default: the first selected well).
      reads_per_well, multi_read_pattern, multi_read_border: multiple reads per well as in
        ``read_absorbance`` (fluorescence beam 2500 µm); i-control requires a 10-100 ms
        settle time. ``standard_deviation`` / ``reads`` hold the detail.

    Saturated wells are ``OVER`` (``math.inf``); wells with too little reference signal ``nan``.
    """
    geometry = _resolve_geometry(plate_definition)
    cells = _selected_cells(plate, wells, geometry)
    _check_reads(flashes, settle_time)
    ex = _tenths(excitation_wavelength)
    em = _tenths(emission_wavelength)
    ex_bw = _tenths(excitation_bandwidth)
    em_bw = _tenths(emission_bandwidth)
    if gain != "optimal" and (
      isinstance(gain, bool) or not isinstance(gain, int) or not 0 <= gain <= 255
    ):
      raise ValueError("gain must be an integer from 0 to 255 or 'optimal'")
    if flash_frequency not in _FLASH_SPEEDS:
      raise ValueError("flash_frequency must be 400, 100 or 'on_the_fly'")
    if z == "optimal" and gain == "optimal":
      raise ValueError("Optimal Z and optimal gain cannot be combined")
    if not isinstance(reads_per_well, int) or reads_per_well < 1:
      raise ValueError("reads_per_well must be a positive integer")
    multi = reads_per_well > 1
    if multi and flash_frequency == "on_the_fly":
      raise ValueError("On-the-fly flashes cannot be combined with multiple reads")
    if multi:
      runs, step_x, step_y = _multi_read_runs(
        geometry, multi_read_pattern, reads_per_well, multi_read_border, 2500
      )
      if not 10 <= settle_time <= 100:
        logger.warning("i-control requires a 10-100 ms settle time with multiple reads")

    def cell_of(well: Well | None) -> tuple[int, int] | None:
      if well is None:
        return None
      if well.parent is not plate:
        raise ValueError("gain_well / z_well must belong to the plate")
      return (well.get_row(), well.get_column())

    passes = _plan_passes(geometry, cells, channels=1)
    ordered_cells = [
      (p.rows[0], c)
      for p in passes
      for c in p.columns
      if p.rows[0] is not None and (p.rows[0], c) in cells
    ]
    label = (f"{ex},{ex_bw},0", f"{em},{em_bw},0")
    settings: dict[str, Any] = dict(
      flashes=flashes,
      flash_frequency=flash_frequency,
      integration_time=integration_time,
      lag_time=lag_time,
      settle_time=settle_time,
      z=None if z == "optimal" else z,
      beam_diameter=True,
    )
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      start = datetime.now()
      started = False
      flashcount: int | None = None
      scans = 0
      try:
        await self._log_position()
        if gain == "optimal":
          started = True
          gain = await self._optimal_gain(
            geometry, passes, cells, label, optimal_gain_percentage, settings, cell_of(gain_well)
          )
          started = False
        if z == "optimal":
          started = True
          z = await self._optimal_z(
            geometry, cell_of(z_well) or ordered_cells[0], label, gain, settings
          )
          settings["z"] = z
          started = False
        temperature, flashcount = await self._configure_fluorescence(
          [label, label], gain=gain, **settings
        )
        started = True
        dark, ref_dark, ref_bright = await self._prepare_fluorescence_reference(ex, gain)
        samples: dict[tuple[int, int], dict[str, tuple[int, int]]] = {}
        n = reads_per_well
        half = (n - 1) / 2
        for pass_index, scan_pass in enumerate(passes):
          row = scan_pass.rows[0]
          xs = [geometry.x(column) for column in scan_pass.columns]
          if multi:
            for column_index, (column, x) in enumerate(zip(scan_pass.columns, xs)):
              if column_index == 0:
                await self._control_command(f"ABSOLUTE MTP,X={x},Y={scan_pass.y}", ("ST",))
                if pass_index == 0:
                  await self._control_command("MOVEMENT MTP,ACCELERATION=LOW", ("ST",))
              for run in runs:
                y = _round_half_up(scan_pass.y + (run[0][1] - half) * step_y)
                run_xs = [_round_half_up(x + (i - half) * step_x) for i, _ in run]
                await self._control_command(f"ABSOLUTE MTP,X={run_xs[0]},Y={y}", ("ST",))
                scans += 1
                blocks = await self._absorbance_burst(
                  f"SCANX {run_xs[0]},{run_xs[-1]},{len(run)}", 528, max_blocks=len(run)
                )
                if len(blocks) != len(run):
                  raise ValueError(f"Expected {len(run)} fluorescence points, got {len(blocks)}")
                for (i, j), block in zip(run, blocks):
                  _, count, reference = _fluorescence_point(block, ex, em)
                  if row is not None and (row, column) in cells:
                    samples.setdefault((row, column), {})[f"{i};{n - 1 - j}"] = (count, reference)
            continue
          await self._control_command(f"ABSOLUTE MTP,Y={scan_pass.y}", ("ST",))
          await self._control_command(f"ABSOLUTE MTP,X={xs[0]},Y={scan_pass.y}", ("ST",))
          scans += 1
          command = f"SCANX {xs[0]},{xs[-1]},{len(xs)}"
          if flash_frequency == "on_the_fly":
            blocks = await self._absorbance_burst(command, 529, max_blocks=1)
            points = _on_the_fly_points(blocks, ex, em, len(xs))
          else:
            blocks = await self._absorbance_burst(command, 528, max_blocks=len(xs))
            if len(blocks) != len(xs):
              raise ValueError(f"Expected {len(xs)} fluorescence points, got {len(blocks)}")
            points = [_fluorescence_point(block, ex, em)[1:] for block in blocks]
          for column, (count, reference) in zip(scan_pass.columns, points):
            if row is not None and (row, column) in cells:
              samples[(row, column)] = {"": (count, reference)}
        if multi:
          await self._control_command("MOVEMENT MTP,ACCELERATION=NORMAL", ("ST",))
        await self._finish_measurement(flashcount, scans)
      except BaseException:
        logger.exception("Fluorescence operation failed")
        if started:
          await self._abort_measurement(flashcount, scans)
        raise
    values: dict[str, float] = {}
    deviation: dict[str, float] = {}
    detail: dict[str, dict[str, float]] = {}
    for cell, reads in sorted(samples.items()):
      well = plate.get_item(cell).get_identifier()
      calculated = {
        key: _fluorescence_value(count, reference, dark, ref_dark, ref_bright)
        for key, (count, reference) in reads.items()
      }
      if not multi:
        values[well] = calculated[""]
        continue
      detail[well] = calculated
      points_ = list(calculated.values())
      if all(math.isfinite(v) for v in points_):
        values[well] = statistics.mean(points_)
        deviation[well] = statistics.stdev(points_)
      else:
        values[well] = deviation[well] = math.nan
    return InfiniteM1000ProFluorescenceResult(
      data=values,
      excitation_wavelength=ex / 10,
      excitation_bandwidth=ex_bw / 10,
      emission_wavelength=em / 10,
      emission_bandwidth=em_bw / 10,
      temperature=temperature,
      start_timestamp=start,
      end_timestamp=datetime.now(),
      gain=gain,
      z=z if isinstance(z, int) else None,
      standard_deviation=deviation,
      reads=detail,
    )

  async def read_fluorescence_spectrum(
    self,
    plate: Plate,
    wells: Sequence[Well] | None = None,
    *,
    scan: Literal["excitation", "emission"],
    start_wavelength: float,
    end_wavelength: float,
    step_size: float,
    excitation_wavelength: float | None = None,
    emission_wavelength: float | None = None,
    plate_definition: str | PlateGeometry,
    excitation_bandwidth: float = 5,
    emission_bandwidth: float = 5,
    gain: int = 100,
    flashes: int = 25,
    flash_frequency: Literal[400, 100] = 400,
    integration_time: int = 20,
    lag_time: int = 0,
    settle_time: int = 0,
    z: int = 20000,
  ) -> FluorescenceSpectrumResult:
    """Scan top fluorescence over excitation or emission wavelengths, well by well.

    For ``scan="excitation"`` pass the fixed ``emission_wavelength``; for ``"emission"`` the
    fixed ``excitation_wavelength``. Values are corrected with the reader's EXCORR/EMCORR
    table averaged over the scanned bandwidth, as i-control does. Spectra are keyed by
    wavelength in nm (whole nm when the step is whole).
    """
    if scan not in ("excitation", "emission"):
      raise ValueError("scan must be 'excitation' or 'emission'")
    fixed = emission_wavelength if scan == "excitation" else excitation_wavelength
    if fixed is None:
      raise ValueError(
        "emission_wavelength is required for an excitation scan"
        if scan == "excitation"
        else "excitation_wavelength is required for an emission scan"
      )
    first, last, step = _tenths(start_wavelength), _tenths(end_wavelength), _tenths(step_size)
    if last <= first or (last - first) % step:
      raise ValueError("end_wavelength must exceed start_wavelength by a multiple of step_size")
    if isinstance(gain, bool) or not isinstance(gain, int) or not 0 <= gain <= 255:
      raise ValueError("gain must be an integer from 0 to 255")
    if flash_frequency not in (400, 100):
      raise ValueError("flash_frequency must be 400 or 100 Hz")
    geometry = _resolve_geometry(plate_definition)
    cells = _selected_cells(plate, wells, geometry)
    _check_reads(flashes, settle_time)
    ex_bw, em_bw = _tenths(excitation_bandwidth), _tenths(emission_bandwidth)
    fixed_raw = _tenths(fixed)
    wavelengths = list(range(first, last + 1, step))
    span = f"{first}~{last}:{step}"
    if scan == "excitation":
      labels = [
        (f"{_EXSCAN_LABEL0_EXCITATION},{ex_bw},0", f"{fixed_raw},{em_bw},0"),
        (f"{span},{ex_bw},0", f"{fixed_raw},{em_bw},0"),
      ]
      prepare_ex = _EXSCAN_LABEL0_EXCITATION
    else:
      labels = [(f"{fixed_raw},{ex_bw},0", f"{span},{em_bw},0")] * 2
      prepare_ex = fixed_raw
    passes = _plan_passes(geometry, cells, channels=1)
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected; call setup first")
      start = datetime.now()
      started = False
      flashcount: int | None = None
      scans = 0
      try:
        await self._log_position()
        table = await self._correction_table("EXCORR" if scan == "excitation" else "EMCORR")
        temperature, flashcount = await self._configure_fluorescence(
          labels,
          gain=gain,
          flashes=flashes,
          flash_frequency=flash_frequency,
          integration_time=integration_time,
          lag_time=lag_time,
          settle_time=settle_time,
          z=z,
          beam_diameter=False,
        )
        started = True
        dark, ref_dark, ref_bright = await self._prepare_fluorescence_reference(prepare_ex, gain)
        spectra: dict[tuple[int, int], dict[Any, float]] = {}
        for scan_pass in passes:
          row = scan_pass.rows[0]
          xs = [geometry.x(column) for column in scan_pass.columns]
          await self._control_command(f"ABSOLUTE MTP,X={xs[0]},Y={scan_pass.y}", ("ST",))
          for column, x in zip(scan_pass.columns, xs):
            await self._control_command(f"ABSOLUTE MTP,X={x},Y={scan_pass.y}", ("ST",))
            count_blocks = len(wavelengths)
            scans += 1
            blocks = await self._absorbance_burst(
              f"SCANX {x},{x},1", 528, max_blocks=count_blocks, timeout=60 + 0.5 * count_blocks
            )
            if len(blocks) != count_blocks:
              raise ValueError(f"Expected {count_blocks} spectrum blocks, got {len(blocks)}")
            for block in blocks:
              label, block_ex, block_em, _, _, count, reference = struct.unpack(">HHHIHHH", block)
              scanned = block_ex if scan == "excitation" else block_em
              other = block_em if scan == "excitation" else block_ex
              if label != 1 or other != fixed_raw or scanned not in wavelengths:
                raise ValueError("Unexpected fluorescence spectrum packet")
              if row is None or (row, column) not in cells:
                continue
              value = _fluorescence_value(count, reference, dark, ref_dark, ref_bright)
              factor = _scan_correction(table, scanned, ex_bw if scan == "excitation" else em_bw)
              key = scanned // 10 if scanned % 10 == 0 else scanned / 10
              spectra.setdefault((row, column), {})[key] = value * factor
        await self._finish_measurement(flashcount, scans)
      except BaseException:
        logger.exception("Fluorescence spectrum failed")
        if started:
          await self._abort_measurement(flashcount, scans)
        raise
    return FluorescenceSpectrumResult(
      data={
        plate.get_item(cell).get_identifier(): dict(sorted(spectrum.items()))
        for cell, spectrum in sorted(spectra.items())
      },
      excitation_wavelength=None if scan == "excitation" else fixed_raw / 10,
      excitation_bandwidth=ex_bw / 10,
      emission_wavelength=None if scan == "emission" else fixed_raw / 10,
      emission_bandwidth=em_bw / 10,
      temperature=temperature,
      start_timestamp=start,
      end_timestamp=datetime.now(),
    )

  async def open(self) -> None:
    """Move the plate carrier out, unless it is already reported out."""
    await self._move("OUT")

  async def close(self) -> None:
    """Move the plate carrier in, unless it is already reported in."""
    await self._move("IN")
