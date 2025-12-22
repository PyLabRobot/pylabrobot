"""Tecan Infinite 200 PRO backend.

This backend targets the Infinite "M" series (e.g., Infinite 200 PRO).  The
"F" series uses a different optical path and is not covered here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence, TextIO, Tuple

from pylabrobot.io.usb import USB
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)
BIN_RE = re.compile(r"^(\d+),BIN:$")


class InfiniteTransport(Protocol):
  """Minimal transport required by the backend.

  Implementations are expected to wrap PyUSB/libusbK.
  """

  async def open(self) -> None:
    """Open the transport connection."""
    ...

  async def close(self) -> None:
    """Close the transport connection."""
    ...

  async def write(self, data: bytes) -> None:
    """Send raw data to the transport."""
    ...

  async def read(self, size: int) -> bytes:
    """Read raw data from the transport."""
    ...

  async def reset(self) -> None:
    """Reset the transport connection."""
    ...


class PyUSBInfiniteTransport(InfiniteTransport):
  """Transport that reuses pylabrobot.io.usb.USB for Infinite communication."""

  def __init__(
    self,
    vendor_id: int = 0x0C47,
    product_id: int = 0x8007,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
  ) -> None:
    self._vendor_id = vendor_id
    self._product_id = product_id
    self._usb: Optional[USB] = None
    self._packet_read_timeout = packet_read_timeout
    self._read_timeout = read_timeout

  async def open(self) -> None:
    io = USB(
      id_vendor=self._vendor_id,
      id_product=self._product_id,
      packet_read_timeout=self._packet_read_timeout,
      read_timeout=self._read_timeout,
    )
    await io.setup()
    self._usb = io

  async def close(self) -> None:
    if self._usb is not None:
      await self._usb.stop()
      self._usb = None

  async def reset(self) -> None:
    await self.close()
    await asyncio.sleep(0.2)
    await self.open()

  async def write(self, data: bytes) -> None:
    if self._usb is None or self._usb.write_endpoint is None:
      raise RuntimeError("USB transport not opened.")
    await self._usb.write(data)

  async def read(self, size: int) -> bytes:
    if self._usb is None:
      raise RuntimeError("USB transport not opened.")
    data = await self._usb.read()
    b = bytes(data[:size])
    return b


@dataclass
class InfiniteScanConfig:
  """Scan configuration for Infinite plate readers."""

  flashes: int = 25
  counts_per_mm_x: float = 1_000
  counts_per_mm_y: float = 1_000


TecanScanConfig = InfiniteScanConfig


def _u16be(payload: bytes, offset: int) -> int:
  return int.from_bytes(payload[offset : offset + 2], "big")


def _u32be(payload: bytes, offset: int) -> int:
  return int.from_bytes(payload[offset : offset + 4], "big")


def _i32be(payload: bytes, offset: int) -> int:
  return int.from_bytes(payload[offset : offset + 4], "big", signed=True)


def _integration_value_to_seconds(value: int) -> float:
  return value / 1_000_000.0 if value >= 1000 else value / 1000.0


def _is_abs_prepare_marker(marker: int) -> bool:
  return marker >= 22 and (marker - 4) % 18 == 0


def _is_abs_data_marker(marker: int) -> bool:
  return marker >= 14 and (marker - 4) % 10 == 0


def _split_payload_and_trailer(marker: int, blob: bytes) -> Optional[Tuple[bytes, Tuple[int, int]]]:
  if len(blob) != marker + 4:
    return None
  payload = blob[:marker]
  trailer = blob[marker:]
  return payload, (_u16be(trailer, 0), _u16be(trailer, 2))


@dataclass(frozen=True)
class _AbsorbancePrepareItem:
  ticker_overflows: int
  ticker_counter: int
  meas_gain: int
  meas_dark: int
  meas_bright: int
  ref_gain: int
  ref_dark: int
  ref_bright: int


@dataclass(frozen=True)
class _AbsorbancePrepare:
  ex: int
  items: List[_AbsorbancePrepareItem]


def _decode_abs_prepare(marker: int, blob: bytes) -> Optional[_AbsorbancePrepare]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4 + 18:
    return None
  ex = _u16be(payload, 2)
  items_blob = payload[4:]
  if len(items_blob) % 18 != 0:
    return None
  items: List[_AbsorbancePrepareItem] = []
  for off in range(0, len(items_blob), 18):
    item = items_blob[off : off + 18]
    items.append(
      _AbsorbancePrepareItem(
        ticker_overflows=_u32be(item, 0),
        ticker_counter=_u16be(item, 4),
        meas_gain=_u16be(item, 6),
        meas_dark=_u16be(item, 8),
        meas_bright=_u16be(item, 10),
        ref_gain=_u16be(item, 12),
        ref_dark=_u16be(item, 14),
        ref_bright=_u16be(item, 16),
      )
    )
  return _AbsorbancePrepare(ex=ex, items=items)


def _decode_abs_data(marker: int, blob: bytes) -> Optional[Tuple[int, int, List[Tuple[int, int]]]]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4:
    return None
  label = _u16be(payload, 0)
  ex = _u16be(payload, 2)
  off = 4
  items: List[Tuple[int, int]] = []
  while off + 10 <= len(payload):
    meas = _u16be(payload, off + 6)
    ref = _u16be(payload, off + 8)
    items.append((meas, ref))
    off += 10
  if off != len(payload):
    return None
  return label, ex, items


def _absorbance_od_calibrated(
  prep: _AbsorbancePrepare, meas_ref_items: List[Tuple[int, int]], od_max: float = 4.0
) -> float:
  if not prep.items:
    raise ValueError("ABS prepare packet contained no calibration items.")

  min_corr_trans = math.pow(10.0, -od_max)

  if len(prep.items) == len(meas_ref_items) and len(prep.items) > 1:
    corr_trans_vals: List[float] = []
    for (meas, ref), cal in zip(meas_ref_items, prep.items):
      denom_corr = cal.meas_bright - cal.meas_dark
      if denom_corr == 0:
        continue
      f_corr = (cal.ref_bright - cal.ref_dark) / denom_corr
      denom = ref - cal.ref_dark
      if denom == 0:
        continue
      corr_trans_vals.append(((meas - cal.meas_dark) / denom) * f_corr)
    if not corr_trans_vals:
      raise ZeroDivisionError("ABS invalid: no usable reads after per-read calibration.")
    corr_trans = max(sum(corr_trans_vals) / len(corr_trans_vals), min_corr_trans)
    return float(-math.log10(corr_trans))

  cal0 = prep.items[0]
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


@dataclass(frozen=True)
class _FluorescencePrepare:
  ex: int
  meas_dark: int
  ref_dark: int
  ref_bright: int


def _decode_flr_prepare(marker: int, blob: bytes) -> Optional[_FluorescencePrepare]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) != 18:
    return None
  return _FluorescencePrepare(
    ex=_u16be(payload, 0),
    meas_dark=_u16be(payload, 10),
    ref_dark=_u16be(payload, 14),
    ref_bright=_u16be(payload, 16),
  )


def _decode_flr_data(
  marker: int, blob: bytes
) -> Optional[Tuple[int, int, int, List[Tuple[int, int]]]]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 6:
    return None
  label = _u16be(payload, 0)
  ex = _u16be(payload, 2)
  em = _u16be(payload, 4)
  off = 6
  items: List[Tuple[int, int]] = []
  while off + 10 <= len(payload):
    meas = _u16be(payload, off + 6)
    ref = _u16be(payload, off + 8)
    items.append((meas, ref))
    off += 10
  if off != len(payload):
    return None
  return label, ex, em, items


def _fluorescence_corrected(
  prep: _FluorescencePrepare, meas_ref_items: List[Tuple[int, int]]
) -> int:
  if not meas_ref_items:
    return 0
  meas_mean = sum(m for m, _ in meas_ref_items) / len(meas_ref_items)
  ref_mean = sum(r for _, r in meas_ref_items) / len(meas_ref_items)
  denom = ref_mean - prep.ref_dark
  if denom == 0:
    return 0
  corr = (meas_mean - prep.meas_dark) * (prep.ref_bright - prep.ref_dark) / denom
  return int(round(corr))


@dataclass(frozen=True)
class _LuminescencePrepare:
  ref_dark: int


def _decode_lum_prepare(marker: int, blob: bytes) -> Optional[_LuminescencePrepare]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) != 10:
    return None
  return _LuminescencePrepare(ref_dark=_i32be(payload, 6))


def _decode_lum_data(marker: int, blob: bytes) -> Optional[Tuple[int, int, List[int]]]:
  split = _split_payload_and_trailer(marker, blob)
  if split is None:
    return None
  payload, _ = split
  if len(payload) < 4:
    return None
  label = _u16be(payload, 0)
  em = _u16be(payload, 2)
  off = 4
  counts: List[int] = []
  while off + 10 <= len(payload):
    counts.append(_i32be(payload, off + 6))
    off += 10
  if off != len(payload):
    return None
  return label, em, counts


def _luminescence_intensity(
  prep: _LuminescencePrepare,
  counts: List[int],
  dark_integration_s: float,
  meas_integration_s: float,
) -> int:
  if not counts:
    return 0
  if dark_integration_s == 0 or meas_integration_s == 0:
    return 0
  count_mean = sum(counts) / len(counts)
  corrected_rate = (count_mean / meas_integration_s) - (prep.ref_dark / dark_integration_s)
  return int(corrected_rate)


StagePosition = Tuple[int, int]


def _consume_leading_ascii_frame(buffer: bytearray) -> Tuple[bool, Optional[str]]:
  """Remove a leading STX...ETX ASCII frame if present."""

  if not buffer or buffer[0] != 0x02:
    return False, None
  end = buffer.find(b"\x03", 1)
  if end == -1:
    return False, None
  # Payload is followed by a 4-byte trailer and optional CR.
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
  marker: Optional[int] = None
  blob: Optional[bytes] = None


class _StreamParser:
  """Parse mixed ASCII and binary packets from the reader."""

  def __init__(
    self,
    *,
    status_frame_len: Optional[int] = None,
    allow_bare_ascii: bool = False,
  ) -> None:
    """Initialize the stream parser."""
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
        events.append(_StreamEvent(marker=self._pending_bin, blob=blob))
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


class _MeasurementDecoder(ABC):
  """Shared incremental decoder for Infinite measurement streams."""

  STATUS_FRAME_LEN: Optional[int] = None

  def __init__(self, expected: int) -> None:
    """Initialize decoder state for a scan with expected measurements."""
    self.expected = expected
    self._terminal_seen = False
    self._parser = _StreamParser(status_frame_len=self.STATUS_FRAME_LEN)

  @property
  @abstractmethod
  def count(self) -> int:
    """Return number of decoded measurements so far."""

  @property
  def done(self) -> bool:
    """Return True if the decoder has seen all expected measurements."""
    return self.count >= self.expected

  def pop_terminal(self) -> bool:
    """Return and clear the terminal frame seen flag."""
    seen = self._terminal_seen
    self._terminal_seen = False
    return seen

  def feed(self, chunk: bytes) -> None:
    """Consume a raw chunk and update decoder state."""
    for event in self._parser.feed(chunk):
      if event.text is not None:
        if event.text == "ST":
          self._terminal_seen = True
      elif event.marker is not None and event.blob is not None:
        self.feed_bin(event.marker, event.blob)

  def feed_bin(self, marker: int, blob: bytes) -> None:
    """Handle a binary payload if the decoder expects one."""
    if self._should_consume_bin(marker):
      self._handle_bin(marker, blob)

  def _should_consume_bin(self, _marker: int) -> bool:
    return False

  def _handle_bin(self, _marker: int, _blob: bytes) -> None:
    return None


class TecanInfinite200ProBackend(PlateReaderBackend):
  """Backend shell for the Infinite 200 PRO."""

  _MODE_CAPABILITY_COMMANDS: Dict[str, List[str]] = {
    "ABS": [
      "#BEAM DIAMETER",
      # Additional capabilities available but currently unused:
      # "#EXCITATION WAVELENGTH",
      # "#EXCITATION USAGE",
      # "#EXCITATION NAME",
      # "#EXCITATION BANDWIDTH",
      # "#EXCITATION ATTENUATION",
      # "#EXCITATION DESCRIPTION",
      # "#TIME READDELAY",
      # "#SHAKING MODE",
      # "#SHAKING CONST.ORBITAL",
      # "#SHAKING AMPLITUDE",
      # "#SHAKING TIME",
      # "#SHAKING CONST.LINEAR",
      # "#TEMPERATURE PLATE",
    ],
    "FI.TOP": [
      # "#BEAM DIAMETER",
      # Additional capabilities available but currently unused:
      # "#EMISSION WAVELENGTH",
      # "#EMISSION USAGE",
      # "#EMISSION NAME",
      # "#EMISSION BANDWIDTH",
      # "#EMISSION ATTENUATION",
      # "#EMISSION DESCRIPTION",
      # "#EXCITATION WAVELENGTH",
      # "#EXCITATION USAGE",
      # "#EXCITATION NAME",
      # "#EXCITATION BANDWIDTH",
      # "#EXCITATION ATTENUATION",
      # "#EXCITATION DESCRIPTION",
      # "#TIME INTEGRATION",
      # "#TIME LAG",
      # "#TIME READDELAY",
      # "#GAIN VALUE",
      # "#READS SPEED",
      # "#READS NUMBER",
      # "#RANGES PMT,EXCITATION",
      # "#RANGES PMT,EMISSION",
      # "#POSITION FIL,Z",
      # "#TEMPERATURE PLATE",
    ],
    "FI.BOTTOM": [
      # "#BEAM DIAMETER",
      # Additional capabilities available but currently unused:
      # "#EMISSION WAVELENGTH",
      # "#EMISSION USAGE",
      # "#EXCITATION WAVELENGTH",
      # "#EXCITATION USAGE",
      # "#TIME INTEGRATION",
      # "#TIME LAG",
      # "#TIME READDELAY",
    ],
    "LUM": [
      # "#BEAM DIAMETER",
      # Additional capabilities available but currently unused:
      # "#EMISSION WAVELENGTH",
      # "#EMISSION USAGE",
      # "#EMISSION NAME",
      # "#EMISSION BANDWIDTH",
      # "#EMISSION ATTENUATION",
      # "#EMISSION DESCRIPTION",
      # "#TIME INTEGRATION",
      # "#TIME READDELAY",
    ],
  }

  def __init__(
    self,
    transport: Optional[InfiniteTransport] = None,
    scan_config: Optional[InfiniteScanConfig] = None,
    packet_log_path: Optional[str] = None,
  ) -> None:
    super().__init__()
    self._transport = transport or PyUSBInfiniteTransport()
    self.config = scan_config or InfiniteScanConfig()
    self._setup_lock = asyncio.Lock()
    self._ready = False
    self._read_chunk_size = 512
    self._max_read_iterations = 200
    self._device_initialized = False
    self._mode_capabilities: Dict[str, Dict[str, str]] = {}
    self._current_fluorescence_excitation: Optional[int] = None
    self._current_fluorescence_emission: Optional[int] = None
    self._lum_integration_s: Dict[int, float] = {}
    self._pending_bin_events: List[Tuple[int, bytes]] = []
    self._ascii_parser = _StreamParser(allow_bare_ascii=True)
    self._run_active = False
    self._active_step_loss_commands: List[str] = []
    self._active_mode: Optional[str] = None
    self._packet_log_path = packet_log_path
    self._packet_log_handle: Optional[TextIO] = None
    self._packet_log_lock = asyncio.Lock()

  async def setup(self) -> None:
    async with self._setup_lock:
      if self._ready:
        return
      await self._transport.open()
      await self._initialize_device()
      for mode in self._MODE_CAPABILITY_COMMANDS:
        if mode not in self._mode_capabilities:
          await self._query_mode_capabilities(mode)
      self._ready = True

  async def stop(self) -> None:
    async with self._setup_lock:
      if not self._ready:
        return
      await self._cleanup_protocol()
      await self._transport.close()
      if self._packet_log_handle is not None:
        self._packet_log_handle.close()
        self._packet_log_handle = None
      self._device_initialized = False
      self._mode_capabilities.clear()
      self._reset_stream_state()
      self._ready = False

  async def open(self) -> None:
    """Open the reader drawer."""

    await self._send_ascii("ABSOLUTE MTP,OUT")
    await self._send_ascii("BY#T5000")

  async def close(self, plate: Optional[Plate]) -> None:  # noqa: ARG002
    """Close the reader drawer."""

    await self._send_ascii("ABSOLUTE MTP,IN")
    await self._send_ascii("BY#T5000")

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    """Queue and execute an absorbance scan."""

    if not 230 <= wavelength <= 1_000:
      raise ValueError("Absorbance wavelength must be between 230 nm and 1000 nm.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=True)

    step_loss = ["CHECK MTP.STEPLOSS", "CHECK ABS.STEPLOSS"]
    self._active_step_loss_commands = list(step_loss)
    self._active_mode = "ABS"
    await self._begin_run()
    try:
      decoder = _AbsorbanceRunDecoder(len(scan_wells))
      await self._configure_absorbance(wavelength)

      for row_index, row_wells in self._group_by_row(ordered_wells):
        start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=True)
        _, y_stage = self._map_well_to_stage(row_wells[0])

        await self._send_ascii(f"ABSOLUTE MTP,Y={y_stage}")
        await self._send_ascii("SCAN DIRECTION=ALTUP")
        await self._send_ascii(
          f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False, read_response=False
        )
        logger.info(
          "Queued scan row %s (%s wells): y=%s, x=%s..%s",
          row_index,
          count,
          y_stage,
          start_x,
          end_x,
        )
        await self._await_measurements(decoder, count, "Absorbance")
        await self._await_scan_terminal(decoder.pop_terminal())

      if len(decoder.measurements) != len(scan_wells):
        raise RuntimeError("Absorbance decoder did not complete scan.")
      intensities: List[float] = []
      prep = decoder.prepare
      if prep is None:
        raise RuntimeError("ABS prepare packet not seen; cannot compute calibrated OD.")
      for meas in decoder.measurements:
        items = meas.items or [(meas.sample, meas.reference)]
        od = _absorbance_od_calibrated(prep, items)
        intensities.append(od)
      matrix = self._format_plate_result(plate, scan_wells, intensities)
      return [
        {
          "wavelength": wavelength,
          "time": time.time(),
          "temperature": None,
          "data": matrix,
        }
      ]
    finally:
      await self._end_run(step_loss)

  async def _configure_absorbance(self, wavelength_nm: int) -> None:
    wl_decitenth = int(round(wavelength_nm * 10))
    bw_decitenth = int(round(self._auto_bandwidth(wavelength_nm) * 10))
    reads_number = max(1, int(self.config.flashes))

    await self._send_ascii("MODE ABS")

    commands = [
      "EXCITATION CLEAR",
      "TIME CLEAR",
      "GAIN CLEAR",
      "READS CLEAR",
      "POSITION CLEAR",
      "MIRROR CLEAR",
      f"EXCITATION 0,ABS,{wl_decitenth},{bw_decitenth},0",
      f"EXCITATION 1,ABS,{wl_decitenth},{bw_decitenth},0",
      f"READS 0,NUMBER={reads_number}",
      f"READS 1,NUMBER={reads_number}",
      "TIME 0,READDELAY=0",
      "TIME 1,READDELAY=0",
      "SCAN DIRECTION=ALTUP",
      "#RATIO LABELS",
      f"BEAM DIAMETER={self._capability_numeric('ABS', '#BEAM DIAMETER', 700)}",
      "RATIO LABELS=1",
      "PREPARE REF",
    ]

    for cmd in commands:
      if cmd == "PREPARE REF":
        await self._send_ascii(cmd, allow_timeout=True, read_response=False)
      else:
        await self._send_ascii(cmd, allow_timeout=True)

  def _auto_bandwidth(self, wavelength_nm: int) -> float:
    """Return bandwidth in nm based on Infinite M specification."""

    return 9.0 if wavelength_nm > 315 else 5.0

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    """Queue and execute a fluorescence scan."""

    if not 230 <= excitation_wavelength <= 850:
      raise ValueError("Excitation wavelength must be between 230 nm and 850 nm.")
    if not 230 <= emission_wavelength <= 850:
      raise ValueError("Emission wavelength must be between 230 nm and 850 nm.")
    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for fluorescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=True)
    step_loss = ["CHECK MTP.STEPLOSS", "CHECK FI.TOP.STEPLOSS", "CHECK FI.STEPLOSS.Z"]
    self._active_step_loss_commands = list(step_loss)
    self._active_mode = "FI.TOP"
    await self._begin_run()
    try:
      await self._configure_fluorescence(excitation_wavelength, emission_wavelength)
      if self._current_fluorescence_excitation is None:
        raise RuntimeError("Fluorescence configuration missing excitation wavelength.")
      decoder = _FluorescenceRunDecoder(
        len(scan_wells),
        self._current_fluorescence_excitation,
        self._current_fluorescence_emission,
      )

      for row_index, row_wells in self._group_by_row(ordered_wells):
        start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=True)
        _, y_stage = self._map_well_to_stage(row_wells[0])

        await self._send_ascii(f"ABSOLUTE MTP,Y={y_stage}")
        await self._send_ascii("SCAN DIRECTION=UP")
        await self._send_ascii(
          f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False, read_response=False
        )
        logger.info(
          "Queued fluorescence scan row %s (%s wells): y=%s, x=%s..%s",
          row_index,
          count,
          y_stage,
          start_x,
          end_x,
        )
        await self._await_measurements(decoder, count, "Fluorescence")
        await self._await_scan_terminal(decoder.pop_terminal())

      if len(decoder.intensities) != len(scan_wells):
        raise RuntimeError("Fluorescence decoder did not complete scan.")
      intensities = decoder.intensities
      matrix = self._format_plate_result(plate, scan_wells, intensities)
      return [
        {
          "ex_wavelength": excitation_wavelength,
          "em_wavelength": emission_wavelength,
          "time": time.time(),
          "temperature": None,
          "data": matrix,
        }
      ]
    finally:
      await self._end_run(step_loss)

  async def _configure_fluorescence(self, excitation_nm: int, emission_nm: int) -> None:
    ex_decitenth = int(round(excitation_nm * 10))
    em_decitenth = int(round(emission_nm * 10))
    self._current_fluorescence_excitation = ex_decitenth
    self._current_fluorescence_emission = em_decitenth
    reads_number = max(1, int(self.config.flashes))
    clear_cmds = [
      "MODE FI.TOP",
      "READS CLEAR",
      "EXCITATION CLEAR",
      "EMISSION CLEAR",
      "TIME CLEAR",
      "GAIN CLEAR",
      "POSITION CLEAR",
      "MIRROR CLEAR",
    ]
    configure_cmds = [
      f"EXCITATION 0,FI,{ex_decitenth},50,0",
      f"EMISSION 0,FI,{em_decitenth},200,0",
      "TIME 0,INTEGRATION=20",
      "TIME 0,LAG=0",
      "TIME 0,READDELAY=0",
      "GAIN 0,VALUE=100",
      "POSITION 0,Z=20000",
      f"BEAM DIAMETER={self._capability_numeric('FI.TOP', '#BEAM DIAMETER', 3000)}",
      "SCAN DIRECTION=UP",
      "RATIO LABELS=1",
      f"READS 0,NUMBER={reads_number}",
      f"EXCITATION 1,FI,{ex_decitenth},50,0",
      f"EMISSION 1,FI,{em_decitenth},200,0",
      "TIME 1,INTEGRATION=20",
      "TIME 1,LAG=0",
      "TIME 1,READDELAY=0",
      "GAIN 1,VALUE=100",
      "POSITION 1,Z=20000",
      f"READS 1,NUMBER={reads_number}",
    ]
    # UI issues the entire FI configuration twice before PREPARE REF.
    for _ in range(2):
      for cmd in clear_cmds:
        await self._send_ascii(cmd, allow_timeout=True)
      for cmd in configure_cmds:
        await self._send_ascii(cmd, allow_timeout=True)
    await self._send_ascii("PREPARE REF", allow_timeout=True, read_response=False)

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
  ) -> List[Dict]:
    """Queue and execute a luminescence scan."""

    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for luminescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=False)
    step_loss = ["CHECK MTP.STEPLOSS", "CHECK LUM.STEPLOSS"]
    self._active_step_loss_commands = list(step_loss)
    self._active_mode = "LUM"
    await self._begin_run()
    try:
      await self._configure_luminescence()
      dark_t = self._lum_integration_s.get(0, 0.0)
      meas_t = self._lum_integration_s.get(1, 0.0)
      decoder = _LuminescenceRunDecoder(
        len(scan_wells),
        dark_integration_s=dark_t,
        meas_integration_s=meas_t,
      )

      for row_index, row_wells in self._group_by_row(ordered_wells):
        start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=False)
        _, y_stage = self._map_well_to_stage(row_wells[0])

        await self._send_ascii(f"ABSOLUTE MTP,Y={y_stage}")
        await self._send_ascii("SCAN DIRECTION=UP")
        await self._send_ascii(
          f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False, read_response=False
        )
        logger.info(
          "Queued luminescence scan row %s (%s wells): y=%s, x=%s..%s",
          row_index,
          count,
          y_stage,
          start_x,
          end_x,
        )
        await self._await_measurements(decoder, count, "Luminescence")
        await self._await_scan_terminal(decoder.pop_terminal())

      if len(decoder.measurements) != len(scan_wells):
        raise RuntimeError("Luminescence decoder did not complete scan.")
      intensities = [measurement.intensity for measurement in decoder.measurements]
      matrix = self._format_plate_result(plate, scan_wells, intensities)
      return [
        {
          "time": time.time(),
          "temperature": None,
          "data": matrix,
        }
      ]
    finally:
      await self._end_run(step_loss)

  async def _await_measurements(
    self, decoder: "_MeasurementDecoder", row_count: int, mode: str
  ) -> None:
    target = decoder.count + row_count
    if self._pending_bin_events:
      for marker, blob in self._pending_bin_events:
        decoder.feed_bin(marker, blob)
      self._pending_bin_events.clear()
    iterations = 0
    while decoder.count < target and iterations < self._max_read_iterations:
      chunk = await self._read_packet(self._read_chunk_size)
      if not chunk:
        raise RuntimeError(f"{mode} read returned empty chunk; transport may not support reads.")
      decoder.feed(chunk)
      iterations += 1
    if decoder.count < target:
      raise RuntimeError(f"Timed out while parsing {mode.lower()} results.")

  async def _await_scan_terminal(self, saw_terminal: bool) -> None:
    if saw_terminal:
      return
    await self._read_ascii_response()

  async def _configure_luminescence(self) -> None:
    await self._send_ascii("MODE LUM")
    # Pre-flight safety checks observed in captures (queries omitted).
    await self._send_ascii("CHECK LUM.FIBER")
    await self._send_ascii("CHECK LUM.LID")
    await self._send_ascii("CHECK LUM.STEPLOSS")
    await self._send_ascii("MODE LUM")
    reads_number = max(1, int(self.config.flashes))
    self._lum_integration_s = {
      0: _integration_value_to_seconds(3_000_000),
      1: _integration_value_to_seconds(1_000_000),
    }
    commands = [
      "READS CLEAR",
      "EMISSION CLEAR",
      "TIME CLEAR",
      "GAIN CLEAR",
      "POSITION CLEAR",
      "MIRROR CLEAR",
      "POSITION LUM,Z=14620",
      "TIME 0,INTEGRATION=3000000",
      f"READS 0,NUMBER={reads_number}",
      "SCAN DIRECTION=UP",
      "RATIO LABELS=1",
      "EMISSION 1,EMPTY,0,0,0",
      "TIME 1,INTEGRATION=1000000",
      "TIME 1,READDELAY=0",
      f"READS 1,NUMBER={reads_number}",
      "#EMISSION ATTENUATION",
      "PREPARE REF",
    ]
    for cmd in commands:
      if cmd == "PREPARE REF":
        await self._send_ascii(cmd, allow_timeout=True, read_response=False)
      else:
        await self._send_ascii(cmd, allow_timeout=True)

  def _group_by_row(self, wells: Sequence[Well]) -> List[Tuple[int, List[Well]]]:
    grouped: Dict[int, List[Well]] = {}
    for well in wells:
      grouped.setdefault(well.get_row(), []).append(well)
    for row in grouped.values():
      row.sort(key=lambda w: w.get_column())
    return sorted(grouped.items(), key=lambda item: item[0])

  def _scan_visit_order(self, wells: Sequence[Well], serpentine: bool) -> List[Well]:
    visit: List[Well] = []
    for row_index, row_wells in self._group_by_row(wells):
      if serpentine and row_index % 2 == 1:
        visit.extend(reversed(row_wells))
      else:
        visit.extend(row_wells)
    return visit

  def _map_well_to_stage(self, well: Well) -> StagePosition:
    if well.location is None:
      raise ValueError("Well does not have a location assigned within its plate definition.")
    center = well.location + well.get_anchor(x="c", y="c")
    cfg = self.config
    stage_x = int(round(center.x * cfg.counts_per_mm_x))
    parent_plate = well.parent
    if parent_plate is None or not isinstance(parent_plate, Plate):
      raise ValueError("Well is not assigned to a plate; cannot derive stage coordinates.")
    plate_height_mm = parent_plate.get_size_y()
    stage_y = int(round((plate_height_mm - center.y) * cfg.counts_per_mm_y))
    return stage_x, stage_y

  def _scan_range(
    self, row_index: int, row_wells: Sequence[Well], serpentine: bool
  ) -> Tuple[int, int, int]:
    """Return start/end/count for a row, honoring serpentine layout when requested."""

    first_x, _ = self._map_well_to_stage(row_wells[0])
    last_x, _ = self._map_well_to_stage(row_wells[-1])
    count = len(row_wells)
    if not serpentine:
      return min(first_x, last_x), max(first_x, last_x), count
    if row_index % 2 == 0:
      return first_x, last_x, count
    return last_x, first_x, count

  def _format_plate_result(
    self, plate: Plate, wells: Sequence[Well], values: Sequence[float]
  ) -> List[List[Optional[float]]]:
    matrix: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for well, val in zip(wells, values):
      r, c = well.get_row(), well.get_column()
      if 0 <= r < plate.num_items_y and 0 <= c < plate.num_items_x:
        matrix[r][c] = float(val)
    return matrix

  async def _initialize_device(self) -> None:
    if self._device_initialized:
      return
    try:
      await self._send_ascii("QQ")
    except TimeoutError:
      logger.warning("QQ produced no response; continuing with initialization.")
    await self._send_ascii("INIT FORCE")
    self._device_initialized = True

  async def _begin_run(self) -> None:
    await self._initialize_device()
    self._reset_stream_state()
    await self._send_ascii("KEYLOCK ON")
    self._run_active = True

  def _reset_stream_state(self) -> None:
    self._pending_bin_events.clear()
    self._ascii_parser = _StreamParser(allow_bare_ascii=True)

  async def _read_packet(self, size: int) -> bytes:
    try:
      data = await self._transport.read(size)
    except TimeoutError:
      await self._recover_transport()
      raise
    if data:
      await self._log_packet("in", data)
    return data

  async def _recover_transport(self) -> None:
    try:
      await self._transport.reset()
    except Exception:
      try:
        await self._transport.close()
        await asyncio.sleep(0.2)
        await self._transport.open()
      except Exception:
        return
    self._device_initialized = False
    self._mode_capabilities.clear()
    self._reset_stream_state()

  async def _log_packet(
    self, direction: str, data: bytes, ascii_payload: Optional[str] = None
  ) -> None:
    if not self._packet_log_path:
      return
    async with self._packet_log_lock:
      if self._packet_log_handle is None:
        parent = os.path.dirname(self._packet_log_path)
        if parent:
          os.makedirs(parent, exist_ok=True)
        self._packet_log_handle = open(self._packet_log_path, "a", encoding="utf-8")
      record = {
        "ts": time.time(),
        "dir": direction,
        "size": len(data),
        "data_hex": data.hex(),
      }
      if ascii_payload is not None:
        record["ascii"] = ascii_payload
      self._packet_log_handle.write(json.dumps(record) + "\n")
      self._packet_log_handle.flush()

  async def _end_run(self, step_loss_commands: Sequence[str]) -> None:
    try:
      await self._send_ascii("TERMINATE", allow_timeout=True)
      for cmd in step_loss_commands:
        await self._send_ascii(cmd, allow_timeout=True)
      await self._send_ascii("KEYLOCK OFF", allow_timeout=True)
      await self._send_ascii("ABSOLUTE MTP,IN", allow_timeout=True)
    finally:
      self._run_active = False
      self._active_step_loss_commands = []
      self._active_mode = None

  async def _cleanup_protocol(self) -> None:
    if not self._run_active and not self._active_step_loss_commands:
      commands = ["KEYLOCK OFF", "ABSOLUTE MTP,IN"]
    else:
      commands = ["TERMINATE", *self._active_step_loss_commands, "KEYLOCK OFF", "ABSOLUTE MTP,IN"]
    for cmd in commands:
      try:
        await self._send_ascii(cmd, allow_timeout=True, read_response=False)
      except Exception:
        logger.warning("Cleanup command failed: %s", cmd)
    self._run_active = False
    self._active_step_loss_commands = []
    self._active_mode = None

  async def _query_mode_capabilities(self, mode: str) -> None:
    commands = self._MODE_CAPABILITY_COMMANDS.get(mode)
    if not commands:
      return
    try:
      await self._send_ascii(f"MODE {mode}")
    except TimeoutError:
      logger.warning("Capability MODE %s timed out; continuing without mode capabilities.", mode)
      return
    collected: Dict[str, str] = {}
    for cmd in commands:
      try:
        frames = await self._send_ascii(cmd)
      except TimeoutError:
        logger.warning("Capability query '%s' timed out; proceeding with defaults.", cmd)
        continue
      if frames:
        collected[cmd] = frames[-1]
    if collected:
      self._mode_capabilities[mode] = collected

  def _get_mode_capability(self, mode: str, command: str) -> Optional[str]:
    return self._mode_capabilities.get(mode, {}).get(command)

  def _capability_numeric(self, mode: str, command: str, fallback: int) -> int:
    resp = self._get_mode_capability(mode, command)
    if not resp:
      return fallback
    token = resp.split("|")[0].split(":")[0].split("~")[0].strip()
    if not token:
      return fallback
    try:
      return int(float(token))
    except ValueError:
      return fallback

  @staticmethod
  def _frame_ascii_command(command: str) -> bytes:
    """Return a framed ASCII payload with length/checksum trailer."""

    payload = command.encode("ascii")
    xor = 0
    for byte in payload:
      xor ^= byte
    checksum = (xor ^ 0x01) & 0xFF
    length = len(payload) & 0xFF
    return b"\x02" + payload + b"\x03\x00\x00" + bytes([length, checksum]) + b"\x0d"

  async def _send_ascii(
    self,
    command: str,
    wait_for_terminal: bool = True,
    allow_timeout: bool = False,
    read_response: bool = True,
  ) -> List[str]:
    logger.debug("[tecan] >> %s", command)
    framed = self._frame_ascii_command(command)
    await self._transport.write(framed)
    await self._log_packet("out", framed, ascii_payload=command)
    if not read_response:
      return []
    if command.startswith(("#", "?")):
      try:
        return await self._read_ascii_response(require_terminal=False)
      except TimeoutError:
        if allow_timeout:
          logger.warning("Timeout waiting for response to %s", command)
          return []
        raise
    try:
      frames = await self._read_ascii_response(require_terminal=wait_for_terminal)
    except TimeoutError:
      if allow_timeout:
        logger.warning("Timeout waiting for response to %s", command)
        return []
      raise
    for pkt in frames:
      logger.debug("[tecan] << %s", pkt)
    return frames

  async def _drain_ascii(self, attempts: int = 4) -> None:
    """Read and discard a few ASCII packets to clear the stream."""
    for _ in range(attempts):
      data = await self._read_packet(128)
      if not data:
        break

  async def _read_ascii_response(
    self, max_iterations: int = 8, require_terminal: bool = True
  ) -> List[str]:
    """Read ASCII frames and cache any binary payloads that arrive."""
    frames: List[str] = []
    saw_terminal = False
    for _ in range(max_iterations):
      chunk = await self._read_packet(128)
      if not chunk:
        break
      for event in self._ascii_parser.feed(chunk):
        if event.text is not None:
          frames.append(event.text)
          if self._is_terminal_frame(event.text):
            saw_terminal = True
        elif event.marker is not None and event.blob is not None:
          self._pending_bin_events.append((event.marker, event.blob))
      if not require_terminal and frames and not self._ascii_parser.has_pending_bin():
        break
      if require_terminal and saw_terminal and not self._ascii_parser.has_pending_bin():
        break
    if require_terminal and not saw_terminal:
      # best effort: drain once more so pending ST doesn't leak into next command
      await self._drain_ascii(1)
    return frames

  @staticmethod
  def _is_terminal_frame(text: str) -> bool:
    """Return True if the ASCII frame is a terminal marker."""
    return text in {"ST", "+", "-"} or text.startswith("BY#T")

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
    self._prepare: Optional[_AbsorbancePrepare] = None

  @property
  def count(self) -> int:
    return len(self.measurements)

  @property
  def prepare(self) -> Optional[_AbsorbancePrepare]:
    """Return the absorbance prepare data, if available."""
    return self._prepare

  def _should_consume_bin(self, marker: int) -> bool:
    return _is_abs_prepare_marker(marker) or _is_abs_data_marker(marker)

  def _handle_bin(self, marker: int, blob: bytes) -> None:
    if _is_abs_prepare_marker(marker):
      if self._prepare is not None:
        return
      decoded = _decode_abs_prepare(marker, blob)
      if decoded is not None:
        self._prepare = decoded
      return
    if _is_abs_data_marker(marker):
      decoded = _decode_abs_data(marker, blob)
      if decoded is None:
        return
      _label, _ex, items = decoded
      sample, reference = items[0] if items else (0, 0)
      self.measurements.append(
        _AbsorbanceMeasurement(sample=sample, reference=reference, items=items)
      )


class _FluorescenceRunDecoder(_MeasurementDecoder):
  """Incrementally decode fluorescence measurement frames."""

  STATUS_FRAME_LEN = 31

  def __init__(
    self,
    expected_wells: int,
    excitation_decitenth: int,
    emission_decitenth: Optional[int],
  ) -> None:
    super().__init__(expected_wells)
    self._excitation = excitation_decitenth
    self._emission = emission_decitenth
    self._intensities: List[int] = []
    self._prepare: Optional[_FluorescencePrepare] = None

  @property
  def count(self) -> int:
    return len(self._intensities)

  @property
  def intensities(self) -> List[int]:
    """Return decoded fluorescence intensities."""
    return self._intensities

  def _should_consume_bin(self, marker: int) -> bool:
    if marker == 18:
      return True
    if marker >= 16 and (marker - 6) % 10 == 0:
      return True
    return False

  def _handle_bin(self, marker: int, blob: bytes) -> None:
    if marker == 18:
      decoded = _decode_flr_prepare(marker, blob)
      if decoded is not None:
        self._prepare = decoded
      return
    decoded = _decode_flr_data(marker, blob)
    if decoded is None:
      return
    _label, _ex, _em, items = decoded
    if self._prepare is not None:
      intensity = _fluorescence_corrected(self._prepare, items)
    else:
      if not items:
        intensity = 0
      else:
        intensity = int(round(sum(m for m, _ in items) / len(items)))
    self._intensities.append(intensity)


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
    self._prepare: Optional[_LuminescencePrepare] = None
    self._dark_integration_s = float(dark_integration_s)
    self._meas_integration_s = float(meas_integration_s)

  @property
  def count(self) -> int:
    return len(self.measurements)

  def _should_consume_bin(self, marker: int) -> bool:
    if marker == 10:
      return True
    if marker >= 14 and (marker - 4) % 10 == 0:
      return True
    return False

  def _handle_bin(self, marker: int, blob: bytes) -> None:
    if marker == 10:
      decoded = _decode_lum_prepare(marker, blob)
      if decoded is not None:
        self._prepare = decoded
      return
    decoded = _decode_lum_data(marker, blob)
    if decoded is None:
      return
    _label, _em, counts = decoded
    if self._prepare is not None and self._dark_integration_s and self._meas_integration_s:
      intensity = _luminescence_intensity(
        self._prepare, counts, self._dark_integration_s, self._meas_integration_s
      )
    else:
      intensity = int(round(sum(counts) / len(counts))) if counts else 0
    self.measurements.append(
      _LuminescenceMeasurement(intensity=intensity)
    )


__all__ = [
  "TecanInfinite200ProBackend",
  "InfiniteScanConfig",
  "PyUSBInfiniteTransport",
]
