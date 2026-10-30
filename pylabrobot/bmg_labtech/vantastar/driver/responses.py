"""Parsers for VANTAstar replies.

A reply is ``[response id] + status header + payload``.  The header's first byte packs its length N
in the low nibble (N counts that byte, so N - 1 status flag bytes follow) with the refused, busy,
error and warning bits (bits 4-7).  The payload starts after the status flag bytes.  Only the
ConfirmationResponse (id 0x01) opens its payload with a device-info section: a count byte, then the
five codes (quit codes of a refused command, warning or error codes, or the busy codes, which are the
id of the command running).  Data replies carry their fields from the first payload byte.

The layouts below hold for firmware 1.11 and 1.12.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

CONFIRMATION_ID = 0x01
MEASUREMENT_DATA_ID = 0x02
TIME_DATA_ID = 0x03
GAIN_DATA_ID = 0x05
WELL_LIST_ID = 0x1D
MW_BLOCK_DATA_ID = 0x2A

_HEADER_LENGTH_MASK = 0x0F

# MeasurementDataResponse payload: what the first byte says the block holds.
_MEASUREMENT_BLOCK = 0x01
# Fixed fields before the per-channel chromatic counts.
_MD_TOTAL_VALUES = 2  # u32
_MD_VALID_VALUES = 6  # u32
_MD_OVERFLOW = 10  # u32, the count a saturated reading shows
_MD_CHANNELS = 15  # u8
# u16: the chromatics of channel 1 in bits 0-11, the spectrometer reference values in bits 12-15;
# with two channels the second channel's count follows as u16.
_MD_CHROMATICS_1 = 16
_MD_CHROMATICS_MASK = 0x0FFF
# Offsets from the wells count (u16): the well a single-well block holds (column, row; 0, 0 for a
# block of every well), its kinetic cycle (u16), the cycle's start (u32, 0.1 s), then the
# temperature (u16) and a block counter (u16) before the values.
_MD_WELL_COLUMN = 9
_MD_WELL_ROW = 10
_MD_CYCLE = 11
_MD_START_TIME = 15
_MD_VALUES = 23

# GainDataResponse payload.
_GD_GAIN = 1  # u16
_GD_FOCUS_CURVE_LENGTH = 11  # u16, 0 when the focus was not adjusted
_GD_FOCAL_HEIGHT = 13  # u16, 0.01 mm

# LayoutWellListDataResponse payload: columns, rows, layout flags (bits 6 and 7: per-well times
# follow each well), reading direction, two move times (u32), moves (u16), then the wells count
# (u16) and each well's column and row in reading order.
_WL_FLAGS = 2
_WL_WELL_TIMES = 0xC0
_WL_WELLS = 14

# MwBlockDataResponse payload: blocks required (u32), block size in values (u32), channels, then
# per channel its sub-block count and 5 bytes per sub-block; then the 0%/100% calibration values
# (u16), blocks stored (u16), blocks sent (u16), the current block (u16) and the values saved in it
# (u32).
_MB_REQUIRED = 0
_MB_SIZE = 4
_MB_CHANNELS = 8
_MB_SUB_BLOCK_SIZE = 5


def split_reply(reply: bytes) -> Tuple[int, bytes, bytes]:
  """Split a reply into its header's first byte, its status flag bytes and its payload.

  Raises:
    ValueError: if the reply is shorter than its header.
  """
  if len(reply) < 2:
    raise ValueError(f"reply too short for a status header: {reply.hex()}")
  first = reply[1]
  length = first & _HEADER_LENGTH_MASK
  if length < 1 or len(reply) < 1 + length:
    raise ValueError(f"reply shorter than its {length}-byte status header: {reply.hex()}")
  return first, reply[2 : 1 + length], reply[1 + length :]


@dataclass(frozen=True)
class Confirmation:
  """The device-info part of a ConfirmationResponse.

  Attributes:
    codes: the five codes; their meaning follows from the header bits (see ``errors.py``).
    temperatures: the three incubator sensor readings in degrees C, None when the reply carries
      none or the reader reports 0 (as it does for a moment after a temperature command).  The
      first is the one Voyager displays.
  """

  codes: Tuple[int, int, int, int, int]
  temperatures: Optional[Tuple[float, float, float]] = None


def parse_confirmation(reply: bytes) -> Confirmation:
  """Parse a ConfirmationResponse's codes and incubator temperatures.

  After the codes come a byte (observed 0) and the incubator block: its length (9), three u16
  temperatures in 0.1 degrees C, then three bytes not decoded here.

  Raises:
    ValueError: if ``reply`` is not a ConfirmationResponse or is shorter than its header.
  """
  if not reply or reply[0] != CONFIRMATION_ID:
    raise ValueError(f"not a ConfirmationResponse: {reply.hex()}")
  _, _, payload = split_reply(reply)
  count = payload[0] if payload else 0
  info = payload[1 : 1 + count]
  codes = tuple(info[i] if i < len(info) else 0 for i in range(5))
  temperatures: Optional[Tuple[float, float, float]] = None
  incubator = payload[1 + count + 1 :]
  if len(incubator) >= 7 and incubator[0] >= 6:
    raw = [int.from_bytes(incubator[1 + 2 * i : 3 + 2 * i], "big") for i in range(3)]
    if any(raw):
      temperatures = (raw[0] / 10, raw[1] / 10, raw[2] / 10)
  return Confirmation(codes=codes, temperatures=temperatures)  # type: ignore[arg-type]


@dataclass
class MeasurementData:
  """A MeasurementDataResponse page: the reader's current data block of a read.

  Each page carries the whole block; ``valid_values`` counts how much of it is measured so far.
  The values are grouped by channel and chromatic, each group one value per measured well in row
  order (A1, A2, ..., B1, ...), then any calibration values (absorbance: per chromatic of channel 1
  and for channel 2, a 100% and a dark value).  A chromatic is a wavelength of an absorbance read
  or spectrum, a fluorescence chromatic, or a point of a fluorescence scan.  An absorbance spectrum
  comes as one block per well, the first with the calibration values.

  Attributes:
    total_values: values in the finished block.
    valid_values: values measured so far.
    overflow: the count a saturated reading shows.
    chromatics: chromatics per channel: absorbance reads every wavelength plus two reference
      wavelengths on channel 1 and one value on the reference channel 2; fluorescence one per
      chromatic or scan point.
    wells: measured wells in the block.
    temperature: degrees C, None when the reader did not monitor it.
    values: the block's values.
    well: the (column, row), 1-based, of a single-well block; None for a block of every well.
    cycle: the kinetic cycle the block holds, from 1.
    start_time: seconds from the read's start to the cycle's start.
  """

  total_values: int
  valid_values: int
  overflow: int
  chromatics: Tuple[int, ...]
  wells: int
  temperature: Optional[float]
  values: List[int] = field(default_factory=list)
  well: Optional[Tuple[int, int]] = None
  cycle: int = 1
  start_time: float = 0.0

  @property
  def complete(self) -> bool:
    """Whether every value of the block is measured."""
    return self.valid_values >= self.total_values

  def group(self, index: int) -> List[int]:
    """The ``index``-th group of one value per well (channel 1's chromatics first)."""
    return self.values[index * self.wells : (index + 1) * self.wells]

  @property
  def calibration(self) -> List[int]:
    """The values after the per-well groups."""
    return self.values[sum(self.chromatics) * self.wells :]


def is_measurement_page(reply: bytes) -> bool:
  """Whether ``reply`` is a MeasurementDataResponse page of a read (not of a focus scan)."""
  if not reply or reply[0] != MEASUREMENT_DATA_ID:
    return False
  try:
    _, _, payload = split_reply(reply)
  except ValueError:
    return False
  return bool(payload) and payload[0] == _MEASUREMENT_BLOCK


def parse_measurement_data(reply: bytes) -> MeasurementData:
  """Parse a MeasurementDataResponse page.

  The values fill the payload to its end; the temperature (u16, 0.1 degrees C) is the field four
  bytes before them.

  Raises:
    ValueError: if ``reply`` is not a measurement page or is truncated.
  """
  if not is_measurement_page(reply):
    raise ValueError(f"not a MeasurementDataResponse page: {reply[:16].hex()}")
  _, _, p = split_reply(reply)

  def u32(offset: int) -> int:
    """The u32 BE at ``offset`` of the payload."""
    return int.from_bytes(p[offset : offset + 4], "big")

  total = u32(_MD_TOTAL_VALUES)
  channels = p[_MD_CHANNELS]
  chromatics_1 = int.from_bytes(p[_MD_CHROMATICS_1 : _MD_CHROMATICS_1 + 2], "big")
  chromatics: Tuple[int, ...] = (chromatics_1 & _MD_CHROMATICS_MASK,)
  wells_offset = _MD_CHROMATICS_1 + 2
  if channels == 2:
    chromatics += (int.from_bytes(p[wells_offset : wells_offset + 2], "big"),)
    wells_offset += 2
  wells = int.from_bytes(p[wells_offset : wells_offset + 2], "big")
  start = len(p) - 4 * total
  if start < wells_offset + _MD_VALUES:
    raise ValueError(f"MeasurementDataResponse too short for {total} values")
  raw_temperature = int.from_bytes(p[start - 4 : start - 2], "big")
  column, row = p[wells_offset + _MD_WELL_COLUMN], p[wells_offset + _MD_WELL_ROW]
  return MeasurementData(
    total_values=total,
    valid_values=u32(_MD_VALID_VALUES),
    overflow=u32(_MD_OVERFLOW),
    chromatics=chromatics,
    wells=wells,
    temperature=raw_temperature / 10 if raw_temperature else None,
    values=[u32(start + 4 * i) for i in range(total)],
    well=(column, row) if column and row else None,
    cycle=int.from_bytes(p[wells_offset + _MD_CYCLE : wells_offset + _MD_CYCLE + 2], "big"),
    start_time=u32(wells_offset + _MD_START_TIME) / 10,
  )


# TimeDataResponse payload (the reply to RUN): TestRunTime, the run time the reader expects in s.
_TD_RUN_TIME = 12  # u32


def parse_run_time(reply: bytes) -> int:
  """The run time in s the reader expects for a read, from the TimeDataResponse it answers a RUN
  with (TestRunTime).

  Raises:
    ValueError: if ``reply`` is not a TimeDataResponse or is truncated.
  """
  if not reply or reply[0] != TIME_DATA_ID:
    raise ValueError(f"not a TimeDataResponse: {reply[:16].hex()}")
  _, _, p = split_reply(reply)
  if len(p) < _TD_RUN_TIME + 4:
    raise ValueError(f"TimeDataResponse too short: {reply.hex()}")
  return int.from_bytes(p[_TD_RUN_TIME : _TD_RUN_TIME + 4], "big")


def parse_well_list(reply: bytes) -> List[Tuple[int, int]]:
  """Parse a LayoutWellListDataResponse: the (column, row), 1-based, of each measured well in the
  order the reader reads them.

  Raises:
    ValueError: if ``reply`` is not a well list, carries per-well times, or is truncated.
  """
  if not reply or reply[0] != WELL_LIST_ID:
    raise ValueError(f"not a LayoutWellListDataResponse: {reply[:16].hex()}")
  _, _, p = split_reply(reply)
  if len(p) < _WL_WELLS + 2:
    raise ValueError(f"LayoutWellListDataResponse too short: {reply.hex()}")
  if p[_WL_FLAGS] & _WL_WELL_TIMES:
    raise ValueError("LayoutWellListDataResponse with per-well times is not supported")
  count = int.from_bytes(p[_WL_WELLS : _WL_WELLS + 2], "big")
  wells = p[_WL_WELLS + 2 : _WL_WELLS + 2 + 2 * count]
  if len(wells) < 2 * count:
    raise ValueError(f"LayoutWellListDataResponse too short for {count} wells")
  return [(wells[2 * i], wells[2 * i + 1]) for i in range(count)]


@dataclass(frozen=True)
class MwBlockStatus:
  """The state of a read's measurement data blocks (MwBlockDataResponse).

  A read stores its data in ``required`` blocks of ``size`` values: one block, or one per kinetic
  cycle.

  Attributes:
    required: blocks the read stores.
    size: values per block, calibration values included.
    calibration_values: the block's 0%/100% calibration values (absorbance).
    stored: blocks finished so far.
    sent: blocks sent so far.
    current: the block being measured, from 1; 0 between blocks.
    saved: values saved so far in the current block.
  """

  required: int
  size: int
  calibration_values: int
  stored: int
  sent: int
  current: int
  saved: int


def parse_mw_block_data(reply: bytes) -> MwBlockStatus:
  """Parse a MwBlockDataResponse.

  Raises:
    ValueError: if ``reply`` is not a MwBlockDataResponse or is truncated.
  """
  if not reply or reply[0] != MW_BLOCK_DATA_ID:
    raise ValueError(f"not a MwBlockDataResponse: {reply[:16].hex()}")
  _, _, p = split_reply(reply)
  offset = _MB_CHANNELS + 1
  if len(p) < offset:
    raise ValueError(f"MwBlockDataResponse too short: {reply.hex()}")
  for _ in range(p[_MB_CHANNELS]):
    if len(p) <= offset:
      raise ValueError(f"MwBlockDataResponse too short: {reply.hex()}")
    offset += 1 + _MB_SUB_BLOCK_SIZE * p[offset]
  if len(p) < offset + 12:
    raise ValueError(f"MwBlockDataResponse too short: {reply.hex()}")

  def u16(o: int) -> int:
    """The u16 BE at ``o`` of the payload."""
    return int.from_bytes(p[o : o + 2], "big")

  return MwBlockStatus(
    required=int.from_bytes(p[_MB_REQUIRED : _MB_REQUIRED + 4], "big"),
    size=int.from_bytes(p[_MB_SIZE : _MB_SIZE + 4], "big"),
    calibration_values=u16(offset),
    stored=u16(offset + 2),
    sent=u16(offset + 4),
    current=u16(offset + 6),
    saved=int.from_bytes(p[offset + 8 : offset + 12], "big"),
  )


def _compute_optical_density(
  sample: int, reference: int, sample_100: int, reference_100: int
) -> float:
  """OD = -log10((sample / sample_100) * (reference_100 / reference)); infinity for a sample of 0.

  Raises:
    ValueError: if a 100% or the reference value is 0.
  """
  if sample_100 == 0 or reference_100 == 0:
    raise ValueError("absorbance block has a 100% value of 0")
  if reference == 0:
    raise ValueError("absorbance block has a reference value of 0")
  transmittance = (sample / sample_100) * (reference_100 / reference)
  return math.inf if transmittance <= 0 else -math.log10(transmittance)


def compute_optical_densities(data: MeasurementData, wavelength_index: int = 0) -> List[float]:
  """The optical density of every well at the ``wavelength_index``-th wavelength.

  OD = -log10((sample / sample_100) * (reference_100 / reference)): the sample is the wavelength's
  group on channel 1, the reference the channel-2 group; the 100% values are the first value of
  their calibration pairs.  A sample of 0 gives infinity.

  Raises:
    ValueError: if the block has no reference channel or a 100% or reference value is 0.
  """
  if len(data.chromatics) != 2:
    raise ValueError("not an absorbance block: no reference channel")
  reference_index = data.chromatics[0]
  calibration = data.calibration
  return [
    _compute_optical_density(
      sample, reference, calibration[2 * wavelength_index], calibration[2 * reference_index]
    )
    for sample, reference in zip(data.group(wavelength_index), data.group(reference_index))
  ]


def compute_spectrum_optical_densities(
  blocks: Sequence[MeasurementData], wavelengths: int
) -> List[List[float]]:
  """The optical densities of an absorbance spectrum: per block (one well each), one per
  wavelength, against the calibration values the first block carries
  (``compute_optical_densities``).

  Raises:
    ValueError: if a block is not a single-well absorbance block, the first carries no calibration
      values, or a 100% or reference value is 0.
  """
  if not blocks or len(blocks[0].chromatics) != 2:
    raise ValueError("not an absorbance spectrum: no blocks or no reference channel")
  reference_index = blocks[0].chromatics[0]
  calibration = blocks[0].calibration
  if len(calibration) < 2 * reference_index + 1:
    raise ValueError("the first block of an absorbance spectrum carries no calibration values")
  densities = []
  for block in blocks:
    if block.wells != 1 or block.chromatics != blocks[0].chromatics:
      raise ValueError("an absorbance spectrum block holds one well of the same chromatics")
    reference = block.values[reference_index]
    densities.append(
      [
        _compute_optical_density(
          block.values[i], reference, calibration[2 * i], calibration[2 * reference_index]
        )
        for i in range(wavelengths)
      ]
    )
  return densities


@dataclass(frozen=True)
class GainFocus:
  """The result of a gain and focus adjustment (GainDataResponse).

  Attributes:
    gain: the gain the adjustment found; 0 when only the focus was adjusted.
    focal_height: the focal height it found, in mm; None when the focus was not adjusted.
  """

  gain: int
  focal_height: Optional[float]


def parse_gain_data(reply: bytes) -> GainFocus:
  """Parse the gain and focal height of a GainDataResponse.

  Raises:
    ValueError: if ``reply`` is not a GainDataResponse or is truncated.
  """
  if not reply or reply[0] != GAIN_DATA_ID:
    raise ValueError(f"not a GainDataResponse: {reply[:16].hex()}")
  _, _, p = split_reply(reply)
  if len(p) < _GD_FOCUS_CURVE_LENGTH + 2:
    raise ValueError(f"GainDataResponse too short: {reply.hex()}")
  curve = int.from_bytes(p[_GD_FOCUS_CURVE_LENGTH : _GD_FOCUS_CURVE_LENGTH + 2], "big")
  if curve and len(p) < _GD_FOCAL_HEIGHT + 2:
    raise ValueError(f"GainDataResponse too short: {reply.hex()}")
  return GainFocus(
    gain=int.from_bytes(p[_GD_GAIN : _GD_GAIN + 2], "big"),
    focal_height=(
      int.from_bytes(p[_GD_FOCAL_HEIGHT : _GD_FOCAL_HEIGHT + 2], "big") / 100 if curve else None
    ),
  )
