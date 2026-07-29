"""Tecan Infinite 200 PRO protocol utilities.

Pure functions for framing, stream parsing, binary decoding, and calibration math.
No I/O -- used by both the driver and capability backends.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from pylabrobot.io.binary import Reader
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

BIN_RE = re.compile(r"^(\d+),BIN:$")

StagePosition = Tuple[int, int]


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def frame_command(command: str) -> bytes:
  """Return a framed command with length/checksum trailer."""
  payload = command.encode("ascii")
  xor = 0
  for byte in payload:
    xor ^= byte
  checksum = (xor ^ 0x01) & 0xFF
  length = len(payload) & 0xFF
  return b"\x02" + payload + b"\x03\x00\x00" + bytes([length, checksum]) + b"\x0d"


def is_terminal_frame(text: str) -> bool:
  """Return True if the ASCII frame is a terminal marker."""
  return text in {"ST", "+", "-"} or text.startswith("BY#T")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _integration_microseconds_to_seconds(value: int) -> float:
  return value / 1_000_000.0


def format_plate_result(
  plate: Plate, wells: Sequence[Well], values: Sequence[float]
) -> List[List[Optional[float]]]:
  """Place per-well values into a 2D ``[row][col]`` matrix matching the plate layout."""
  matrix: List[List[Optional[float]]] = [
    [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
  ]
  for well, val in zip(wells, values):
    r, c = well.get_row(), well.get_column()
    if 0 <= r < plate.num_items_y and 0 <= c < plate.num_items_x:
      matrix[r][c] = float(val)
  return matrix


def _split_payload_and_trailer(
  payload_len: int, blob: bytes
) -> Optional[Tuple[bytes, Tuple[int, int]]]:
  if len(blob) != payload_len + 4:
    return None
  payload = blob[:payload_len]
  trailer_reader = Reader(blob[payload_len:], little_endian=False)
  return payload, (trailer_reader.u16(), trailer_reader.u16())


# ---------------------------------------------------------------------------
# Stream parsing
# ---------------------------------------------------------------------------


def _consume_leading_ascii_frame(buffer: bytearray) -> Tuple[bool, Optional[str]]:
  """Remove a leading STX...ETX ASCII frame if present."""
  if not buffer or buffer[0] != 0x02:
    return False, None
  end = buffer.find(b"\x03", 1)
  if end == -1:
    return False, None
  if len(buffer) < end + 5:
    return False, None
  text = buffer[1:end].decode("ascii", "ignore")
  del buffer[: end + 5]
  if buffer and buffer[0] == 0x0D:
    del buffer[0]
  return True, text


def _consume_status_frame(buffer: bytearray, length: int) -> bool:
  """Drop a leading ESC-prefixed status frame if present."""
  if len(buffer) >= length and buffer[0] == 0x1B:
    del buffer[:length]
    return True
  return False


@dataclass
class _StreamEvent:
  """Parsed stream event (ASCII or binary)."""

  text: Optional[str] = None
  payload_len: Optional[int] = None
  blob: Optional[bytes] = None


class _StreamParser:
  """Parse mixed ASCII and binary packets from the reader."""

  def __init__(
    self,
    *,
    status_frame_len: Optional[int] = None,
    allow_bare_ascii: bool = False,
  ) -> None:
    self._buffer = bytearray()
    self._pending_bin: Optional[int] = None
    self._status_frame_len = status_frame_len
    self._allow_bare_ascii = allow_bare_ascii

  def has_pending_bin(self) -> bool:
    """Return True if a binary payload length is pending."""
    return self._pending_bin is not None

  def feed(self, chunk: bytes) -> List[_StreamEvent]:
    """Feed raw bytes and return newly parsed events."""
    self._buffer.extend(chunk)
    events: List[_StreamEvent] = []
    progressed = True
    while progressed:
      progressed = False
      if self._pending_bin is not None:
        need = self._pending_bin + 4
        if len(self._buffer) < need:
          break
        blob = bytes(self._buffer[:need])
        del self._buffer[:need]
        events.append(_StreamEvent(payload_len=self._pending_bin, blob=blob))
        self._pending_bin = None
        progressed = True
        continue
      if self._status_frame_len and _consume_status_frame(self._buffer, self._status_frame_len):
        progressed = True
        continue
      consumed, text = _consume_leading_ascii_frame(self._buffer)
      if consumed:
        events.append(_StreamEvent(text=text))
        if text:
          m = BIN_RE.match(text)
          if m:
            self._pending_bin = int(m.group(1))
        progressed = True
        continue
      if self._allow_bare_ascii and self._buffer and all(32 <= b <= 126 for b in self._buffer):
        text = self._buffer.decode("ascii", "ignore")
        self._buffer.clear()
        events.append(_StreamEvent(text=text))
        progressed = True
        continue
    return events


# ---------------------------------------------------------------------------
# Measurement decoder base
# ---------------------------------------------------------------------------


class _MeasurementDecoder(ABC):
  """Shared incremental decoder for Infinite measurement streams."""

  STATUS_FRAME_LEN: Optional[int] = None

  def __init__(self, expected: int) -> None:
    self.expected = expected
    self._terminal_seen = False
    self._parser = _StreamParser(status_frame_len=self.STATUS_FRAME_LEN)

  @property
  @abstractmethod
  def count(self) -> int:
    """Return number of decoded measurements so far."""

  @property
  def done(self) -> bool:
    return self.count >= self.expected

  def pop_terminal(self) -> bool:
    seen = self._terminal_seen
    self._terminal_seen = False
    return seen

  def feed(self, chunk: bytes) -> None:
    for event in self._parser.feed(chunk):
      if event.text is not None:
        if event.text == "ST":
          self._terminal_seen = True
      elif event.payload_len is not None and event.blob is not None:
        self.feed_bin(event.payload_len, event.blob)

  def feed_bin(self, payload_len: int, blob: bytes) -> None:
    if self._should_consume_bin(payload_len):
      self._handle_bin(payload_len, blob)

  def _should_consume_bin(self, _payload_len: int) -> bool:
    return False

  def _handle_bin(self, _payload_len: int, _blob: bytes) -> None:
    return None


# ---------------------------------------------------------------------------
# Absorbance decoding & calibration
# ---------------------------------------------------------------------------


def _is_abs_calibration_len(payload_len: int) -> bool:
  return payload_len >= 22 and (payload_len - 4) % 18 == 0


def _is_abs_data_len(payload_len: int) -> bool:
  return payload_len >= 14 and (payload_len - 4) % 10 == 0


@dataclass(frozen=True)
class _AbsorbanceCalibrationItem:
  ticker_overflows: int
  ticker_counter: int
  meas_gain: int
  meas_dark: int
  meas_bright: int
  ref_gain: int
  ref_dark: int
  ref_bright: int


@dataclass(frozen=True)
class _AbsorbanceCalibration:
  ex: int
  items: List[_AbsorbanceCalibrationItem]


def _decode_abs_calibration(payload_len: int, blob: bytes) -> Optional[_AbsorbanceCalibration]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4 + 18:
    return None
  if (len(payload) - 4) % 18 != 0:
    return None
  reader = Reader(payload, little_endian=False)
  reader.raw_bytes(2)
  ex = reader.u16()
  items: List[_AbsorbanceCalibrationItem] = []
  while reader.has_remaining():
    items.append(
      _AbsorbanceCalibrationItem(
        ticker_overflows=reader.u32(),
        ticker_counter=reader.u16(),
        meas_gain=reader.u16(),
        meas_dark=reader.u16(),
        meas_bright=reader.u16(),
        ref_gain=reader.u16(),
        ref_dark=reader.u16(),
        ref_bright=reader.u16(),
      )
    )
  return _AbsorbanceCalibration(ex=ex, items=items)


def _decode_abs_data(
  payload_len: int, blob: bytes
) -> Optional[Tuple[int, int, List[Tuple[int, int]]]]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4:
    return None
  reader = Reader(payload, little_endian=False)
  label = reader.u16()
  ex = reader.u16()
  items: List[Tuple[int, int]] = []
  while reader.offset() + 10 <= len(payload):
    reader.raw_bytes(6)
    meas = reader.u16()
    ref = reader.u16()
    items.append((meas, ref))
  if reader.offset() != len(payload):
    return None
  return label, ex, items


def _absorbance_od_calibrated(
  cal: _AbsorbanceCalibration, meas_ref_items: List[Tuple[int, int]], od_max: float = 4.0
) -> float:
  if not cal.items:
    raise ValueError("ABS calibration packet contained no calibration items.")

  min_corr_trans = math.pow(10.0, -od_max)

  if len(cal.items) == len(meas_ref_items) and len(cal.items) > 1:
    corr_trans_vals: List[float] = []
    for (meas, ref), cal_item in zip(meas_ref_items, cal.items):
      denom_corr = cal_item.meas_bright - cal_item.meas_dark
      if denom_corr == 0:
        continue
      f_corr = (cal_item.ref_bright - cal_item.ref_dark) / denom_corr
      denom = ref - cal_item.ref_dark
      if denom == 0:
        continue
      corr_trans_vals.append(((meas - cal_item.meas_dark) / denom) * f_corr)
    if not corr_trans_vals:
      raise ZeroDivisionError("ABS invalid: no usable reads after per-read calibration.")
    corr_trans = max(sum(corr_trans_vals) / len(corr_trans_vals), min_corr_trans)
    return float(-math.log10(corr_trans))

  cal0 = cal.items[0]
  denom_corr = cal0.meas_bright - cal0.meas_dark
  if denom_corr == 0:
    raise ZeroDivisionError("ABS calibration invalid: meas_bright == meas_dark")
  f_corr = (cal0.ref_bright - cal0.ref_dark) / denom_corr

  trans_vals: List[float] = []
  for meas, ref in meas_ref_items:
    denom = ref - cal0.ref_dark
    if denom == 0:
      continue
    trans_vals.append((meas - cal0.meas_dark) / denom)
  if not trans_vals:
    raise ZeroDivisionError("ABS invalid: all ref reads equal ref_dark")

  trans_mean = sum(trans_vals) / len(trans_vals)
  corr_trans = max(trans_mean * f_corr, min_corr_trans)
  return float(-math.log10(corr_trans))


@dataclass
class _AbsorbanceMeasurement:
  sample: int
  reference: int
  items: Optional[List[Tuple[int, int]]] = None


class _AbsorbanceRunDecoder(_MeasurementDecoder):
  """Incrementally decode absorbance measurement frames."""

  STATUS_FRAME_LEN = 31

  def __init__(self, expected: int) -> None:
    super().__init__(expected)
    self.measurements: List[_AbsorbanceMeasurement] = []
    self._calibration: Optional[_AbsorbanceCalibration] = None

  @property
  def count(self) -> int:
    return len(self.measurements)

  @property
  def calibration(self) -> Optional[_AbsorbanceCalibration]:
    return self._calibration

  def _should_consume_bin(self, payload_len: int) -> bool:
    return _is_abs_calibration_len(payload_len) or _is_abs_data_len(payload_len)

  def _handle_bin(self, payload_len: int, blob: bytes) -> None:
    if _is_abs_calibration_len(payload_len):
      if self._calibration is not None:
        return
      cal = _decode_abs_calibration(payload_len, blob)
      if cal is not None:
        self._calibration = cal
      return
    if _is_abs_data_len(payload_len):
      data = _decode_abs_data(payload_len, blob)
      if data is None:
        return
      _label, _ex, items = data
      sample, reference = items[0] if items else (0, 0)
      self.measurements.append(
        _AbsorbanceMeasurement(sample=sample, reference=reference, items=items)
      )


# ---------------------------------------------------------------------------
# Fluorescence decoding & calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FluorescenceCalibration:
  ex: int
  meas_dark: int
  ref_dark: int
  ref_bright: int


def _decode_flr_calibration(payload_len: int, blob: bytes) -> Optional[_FluorescenceCalibration]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) != 18:
    return None
  reader = Reader(payload, little_endian=False)
  ex = reader.u16()
  reader.raw_bytes(8)
  meas_dark = reader.u16()
  reader.raw_bytes(2)
  ref_dark = reader.u16()
  ref_bright = reader.u16()
  return _FluorescenceCalibration(
    ex=ex,
    meas_dark=meas_dark,
    ref_dark=ref_dark,
    ref_bright=ref_bright,
  )


def _decode_flr_data(
  payload_len: int, blob: bytes
) -> Optional[Tuple[int, int, int, List[Tuple[int, int]]]]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 6:
    return None
  reader = Reader(payload, little_endian=False)
  label = reader.u16()
  ex = reader.u16()
  em = reader.u16()
  items: List[Tuple[int, int]] = []
  while reader.offset() + 10 <= len(payload):
    reader.raw_bytes(6)
    meas = reader.u16()
    ref = reader.u16()
    items.append((meas, ref))
  if reader.offset() != len(payload):
    return None
  return label, ex, em, items


def _fluorescence_corrected(
  cal: _FluorescenceCalibration, meas_ref_items: List[Tuple[int, int]]
) -> int:
  if not meas_ref_items:
    return 0
  meas_mean = sum(m for m, _ in meas_ref_items) / len(meas_ref_items)
  ref_mean = sum(r for _, r in meas_ref_items) / len(meas_ref_items)
  denom = ref_mean - cal.ref_dark
  if denom == 0:
    return 0
  corr = (meas_mean - cal.meas_dark) * (cal.ref_bright - cal.ref_dark) / denom
  return int(round(corr))


class _FluorescenceRunDecoder(_MeasurementDecoder):
  """Incrementally decode fluorescence measurement frames."""

  STATUS_FRAME_LEN = 31

  def __init__(self, expected_wells: int) -> None:
    super().__init__(expected_wells)
    self._intensities: List[int] = []
    self._calibration: Optional[_FluorescenceCalibration] = None

  @property
  def count(self) -> int:
    return len(self._intensities)

  @property
  def intensities(self) -> List[int]:
    return self._intensities

  def _should_consume_bin(self, payload_len: int) -> bool:
    if payload_len == 18:
      return True
    if payload_len >= 16 and (payload_len - 6) % 10 == 0:
      return True
    return False

  def _handle_bin(self, payload_len: int, blob: bytes) -> None:
    if payload_len == 18:
      cal = _decode_flr_calibration(payload_len, blob)
      if cal is not None:
        self._calibration = cal
      return
    data = _decode_flr_data(payload_len, blob)
    if data is None:
      return
    _label, _ex, _em, items = data
    if self._calibration is not None:
      intensity = _fluorescence_corrected(self._calibration, items)
    else:
      if not items:
        intensity = 0
      else:
        intensity = int(round(sum(m for m, _ in items) / len(items)))
    self._intensities.append(intensity)


# ---------------------------------------------------------------------------
# Luminescence decoding & calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LuminescenceCalibration:
  ref_dark: int


def _decode_lum_calibration(payload_len: int, blob: bytes) -> Optional[_LuminescenceCalibration]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) != 10:
    return None
  reader = Reader(payload, little_endian=False)
  reader.raw_bytes(6)
  return _LuminescenceCalibration(ref_dark=reader.i32())


def _decode_lum_data(payload_len: int, blob: bytes) -> Optional[Tuple[int, int, List[int]]]:
  split = _split_payload_and_trailer(payload_len, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4:
    return None
  reader = Reader(payload, little_endian=False)
  label = reader.u16()
  em = reader.u16()
  counts: List[int] = []
  while reader.offset() + 10 <= len(payload):
    reader.raw_bytes(6)
    counts.append(reader.i32())
  if reader.offset() != len(payload):
    return None
  return label, em, counts


def _luminescence_intensity(
  cal: _LuminescenceCalibration,
  counts: List[int],
  dark_integration_s: float,
  meas_integration_s: float,
) -> int:
  if not counts:
    return 0
  if dark_integration_s == 0 or meas_integration_s == 0:
    return 0
  count_mean = sum(counts) / len(counts)
  corrected_rate = (count_mean / meas_integration_s) - (cal.ref_dark / dark_integration_s)
  return int(corrected_rate)


@dataclass
class _LuminescenceMeasurement:
  intensity: int


class _LuminescenceRunDecoder(_MeasurementDecoder):
  """Incrementally decode luminescence measurement frames."""

  def __init__(
    self,
    expected: int,
    *,
    dark_integration_s: float = 0.0,
    meas_integration_s: float = 0.0,
  ) -> None:
    super().__init__(expected)
    self.measurements: List[_LuminescenceMeasurement] = []
    self._calibration: Optional[_LuminescenceCalibration] = None
    self._dark_integration_s = float(dark_integration_s)
    self._meas_integration_s = float(meas_integration_s)

  @property
  def count(self) -> int:
    return len(self.measurements)

  def _should_consume_bin(self, payload_len: int) -> bool:
    if payload_len == 10:
      return True
    if payload_len >= 14 and (payload_len - 4) % 10 == 0:
      return True
    return False

  def _handle_bin(self, payload_len: int, blob: bytes) -> None:
    if payload_len == 10:
      cal = _decode_lum_calibration(payload_len, blob)
      if cal is not None:
        self._calibration = cal
      return
    data = _decode_lum_data(payload_len, blob)
    if data is None:
      return
    _label, _em, counts = data
    if self._calibration is not None and self._dark_integration_s and self._meas_integration_s:
      intensity = _luminescence_intensity(
        self._calibration, counts, self._dark_integration_s, self._meas_integration_s
      )
    else:
      intensity = int(round(sum(counts) / len(counts))) if counts else 0
    self.measurements.append(_LuminescenceMeasurement(intensity=intensity))
