"""Tecan Infinite 200 PRO backend.

This backend targets the Infinite "M" series (e.g., Infinite 200 PRO).  The
"F" series uses a different optical path and is not covered here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple, cast

from pylabrobot.io.binary import Reader
from pylabrobot.io.usb import USB
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)
BIN_RE = re.compile(r"^(\d+),BIN:$")

TecanInfinitePlatePosition = Literal[
  "UNKNOWN", "INIT", "HOME", "IN", "OUT", "HEATING", "SHAKING", "FLOATING"
]
TecanInfinitePlateSensorState = Literal["FREE", "TAKEN", "UNDEFINED"]
TecanInfiniteTemperatureStatus = Literal["ON", "OFF"]
TecanInfiniteShakingMode = Literal["LINEAR", "ORBITAL"]
TecanInfiniteInstrumentState = Literal[
  "standby", "power_down", "power_up", "parked", "busy", "busy_in_background", "message", "unknown"
]


class TecanInfiniteResponseError(RuntimeError):
  """Reports that reader communication did not confirm a query result or requested change."""

  def __init__(self, command: str, responses: Sequence[str], reason: str) -> None:
    self.command = command
    self.responses = tuple(responses)
    self.reason = reason
    super().__init__(f"Tecan Infinite response to {command!r} {reason}: {self.responses!r}")


@dataclass(frozen=True)
class TecanInfiniteInstrumentStatus:
  """Contains the interpreted reader state and the unmodified device response."""

  state: TecanInfiniteInstrumentState
  raw: str


def _integration_microseconds_to_seconds(value: int) -> float:
  # DLL/UI indicates integration time is stored in microseconds; UI displays ms by dividing by 1000.
  return value / 1_000_000.0


def _is_abs_calibration_len(payload_len: int) -> bool:
  return payload_len >= 22 and (payload_len - 4) % 18 == 0


def _is_abs_data_len(payload_len: int) -> bool:
  return payload_len >= 14 and (payload_len - 4) % 10 == 0


def _split_payload_and_trailer(
  payload_len: int, blob: bytes
) -> Optional[Tuple[bytes, Tuple[int, int]]]:
  if len(blob) != payload_len + 4:
    return None
  payload = blob[:payload_len]
  trailer_reader = Reader(blob[payload_len:], little_endian=False)
  return payload, (trailer_reader.u16(), trailer_reader.u16())


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
  reader.raw_bytes(2)  # skip first 2 bytes
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
    reader.raw_bytes(6)  # skip first 6 bytes of each item
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
  reader.raw_bytes(8)  # skip bytes 2-9
  meas_dark = reader.u16()
  reader.raw_bytes(2)  # skip bytes 12-13
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
    reader.raw_bytes(6)  # skip first 6 bytes of each item
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
  reader.raw_bytes(6)  # skip bytes 0-5
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
    reader.raw_bytes(6)  # skip first 6 bytes of each item
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
      elif event.payload_len is not None and event.blob is not None:
        self.feed_bin(event.payload_len, event.blob)

  def feed_bin(self, payload_len: int, blob: bytes) -> None:
    """Handle a binary payload if the decoder expects one."""
    if self._should_consume_bin(payload_len):
      self._handle_bin(payload_len, blob)

  def _should_consume_bin(self, _payload_len: int) -> bool:
    return False

  def _handle_bin(self, _payload_len: int, _blob: bytes) -> None:
    return None


class ExperimentalTecanInfinite200ProBackend(PlateReaderBackend):
  """Backend shell for the Infinite 200 PRO."""

  _PLATE_POSITIONS = {"UNKNOWN", "INIT", "HOME", "IN", "OUT", "HEATING", "SHAKING", "FLOATING"}
  _STATUS_STATES: Dict[str, TecanInfiniteInstrumentState] = {
    "ST": "standby",
    "PD": "power_down",
    "PU": "power_up",
    "PA": "parked",
  }
  _BUSY_STATUS_RE = re.compile(r"^(?P<background>\+)?BY(?:#.[0-9]+|%[0-9]+|\$.*)?$")

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

  VENDOR_ID = 0x0C47
  PRODUCT_ID = 0x8007

  def __init__(
    self,
    counts_per_mm_x: float = 1_000,
    counts_per_mm_y: float = 1_000,
    counts_per_mm_z: float = 1_000,
  ) -> None:
    super().__init__()
    self.io = USB(
      id_vendor=self.VENDOR_ID,
      id_product=self.PRODUCT_ID,
      human_readable_device_name="Tecan Infinite 200 PRO",
      packet_read_timeout=3,
      read_timeout=30,
    )
    self.counts_per_mm_x = counts_per_mm_x
    self.counts_per_mm_y = counts_per_mm_y
    self.counts_per_mm_z = counts_per_mm_z
    self._setup_lock = asyncio.Lock()
    self._ready = False
    self._read_chunk_size = 512
    self._max_row_wait_s = 300.0
    self._mode_capabilities: Dict[str, Dict[str, str]] = {}
    self._pending_bin_events: List[Tuple[int, bytes]] = []
    self._parser = _StreamParser(allow_bare_ascii=True)
    self._run_active = False
    self._active_step_loss_commands: List[str] = []

  async def setup(self) -> None:
    async with self._setup_lock:
      if self._ready:
        return
      await self.io.setup()
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
      await self.io.stop()
      self._mode_capabilities.clear()
      self._reset_stream_state()
      self._ready = False

  async def open(self) -> None:
    """Open the reader drawer."""

    await self._move_plate_transport("OUT")

  async def close(self, plate: Optional[Plate]) -> None:
    """Close the reader drawer."""

    await self._move_plate_transport("IN")

  async def request_plate_position(self) -> TecanInfinitePlatePosition:
    """Read the position that the reader reports for the plate transport.

    The result can be ``IN``, ``OUT``, or another device state. It does not show whether a
    microplate is present. It does not prove that the transport moved without an obstruction.
    """

    command = "?ABSOLUTE MTP,POS"
    response = await self._request_state(command)
    if response not in self._PLATE_POSITIONS:
      raise TecanInfiniteResponseError(command, [response], "contained an unknown position")
    return cast(TecanInfinitePlatePosition, response)

  async def request_plate_sensor_state(self) -> TecanInfinitePlateSensorState:
    """Read the plate-position sensor state.

    After a successful inward movement, ``FREE`` means that the sensor position is unoccupied.
    ``TAKEN`` means that a microplate occupies the sensor position. ``UNDEFINED`` means that the
    reader does not report a known sensor state. Do not interpret ``UNDEFINED`` as an absent
    microplate. While the tray is out, the reader can retain the result from the prior inward
    movement.
    """

    command = "?SENSOR PLATEPOS"
    response = await self._request_state(command)
    if response not in {"FREE", "TAKEN", "UNDEFINED"}:
      raise TecanInfiniteResponseError(command, [response], "contained an unknown sensor state")
    return cast(TecanInfinitePlateSensorState, response)

  async def request_current_temperature(self) -> float:
    """Read the current plate temperature in degrees Celsius."""

    return await self._request_temperature("CURRENT")

  async def request_target_temperature(self) -> float:
    """Read the target plate temperature in degrees Celsius."""

    return await self._request_temperature("TARGET")

  async def request_temperature_status(self) -> TecanInfiniteTemperatureStatus:
    """Read whether plate-temperature control is on or off."""

    return await self._request_temperature_status()

  async def set_temperature(self, temperature: float) -> None:
    """Set the plate-temperature target. Start temperature control.

    The reader accepts command values from 0.0 to 42.0 degrees Celsius in 0.1-degree increments.
    Tecan specifies an achievable heating range from ambient temperature plus 5 degrees Celsius
    to 42 degrees Celsius. This method reads the applied target and the control status before it
    returns.
    """

    raw_temperature = self._encode_temperature(temperature)
    await self._send_control_command(f"TEMPERATURE PLATE,TARGET={raw_temperature}")
    applied_temperature = await self._request_temperature("TARGET", recover_on_timeout=False)
    if applied_temperature != raw_temperature / 10.0:
      raise TecanInfiniteResponseError(
        "?TEMPERATURE PLATE,TARGET",
        [str(applied_temperature)],
        f"did not match the requested target {raw_temperature / 10.0}",
      )

    await self._send_control_command("TEMPERATURE PLATE,STATUS=ON")
    applied_status = await self._request_temperature_status(recover_on_timeout=False)
    if applied_status != "ON":
      raise TecanInfiniteResponseError(
        "?TEMPERATURE PLATE,STATUS",
        [applied_status],
        "did not confirm that temperature control was enabled",
      )

  async def stop_temperature_control(self) -> None:
    """Stop plate-temperature control. Confirm that the reader reports ``OFF``."""

    await self._send_control_command("TEMPERATURE PLATE,STATUS=OFF")
    applied_status = await self._request_temperature_status(recover_on_timeout=False)
    if applied_status != "OFF":
      raise TecanInfiniteResponseError(
        "?TEMPERATURE PLATE,STATUS",
        [applied_status],
        "did not confirm that temperature control was disabled",
      )

  async def request_shaking_mode(self) -> TecanInfiniteShakingMode:
    """Read the configured shaking mode."""

    command = "?SHAKING MODE"
    response = await self._request_state(command)
    if response not in {"LINEAR", "ORBITAL"}:
      raise TecanInfiniteResponseError(command, [response], "contained an unknown shaking mode")
    return cast(TecanInfiniteShakingMode, response)

  async def request_shaking_duration(self) -> Optional[int]:
    """Read the shaking duration in seconds. Return ``None`` if the reader reports ``-1``."""

    duration = await self._request_integer_state("?SHAKING TIME", "shaking duration")
    if duration == -1:
      return None
    if duration < 0:
      raise TecanInfiniteResponseError(
        "?SHAKING TIME", [str(duration)], "contained an invalid shaking duration"
      )
    return duration

  async def request_shaking_amplitude(self) -> Optional[float]:
    """Read the shaking amplitude in millimeters. Return ``None`` if the reader reports ``-1``."""

    raw_amplitude = await self._request_integer_state("?SHAKING AMPLITUDE", "shaking amplitude")
    if raw_amplitude == -1:
      return None
    if raw_amplitude < 0:
      raise TecanInfiniteResponseError(
        "?SHAKING AMPLITUDE", [str(raw_amplitude)], "contained an invalid shaking amplitude"
      )
    return raw_amplitude / 1000.0

  async def shake(
    self,
    duration: int,
    mode: TecanInfiniteShakingMode = "ORBITAL",
    amplitude: float = 1.0,
  ) -> None:
    """Start a timed plate shake. Return after the reader reports standby.

    Set ``duration`` from 1 to 999 seconds. Set ``mode`` to ``LINEAR`` or ``ORBITAL``. Set
    ``amplitude`` from 1.0 to 6.0 millimeters in 0.5-millimeter increments. The reader calculates
    the frequency from the mode and amplitude. Cancellation does not stop the timed action after
    the reader accepts the ``SHAKING ON`` command.
    """

    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 999:
      raise ValueError("Shaking duration must be an integer from 1 to 999 seconds.")
    if mode not in {"LINEAR", "ORBITAL"}:
      raise ValueError("Shaking mode must be 'LINEAR' or 'ORBITAL'.")
    if not math.isfinite(amplitude) or not 1.0 <= amplitude <= 6.0:
      raise ValueError("Shaking amplitude must be from 1 to 6 mm.")
    raw_amplitude = round(amplitude * 1000)
    if not math.isclose(raw_amplitude / 1000.0, amplitude) or raw_amplitude % 500 != 0:
      raise ValueError("Shaking amplitude must use 0.5 mm increments.")

    await self._set_shaking_mode(mode)
    await self._set_shaking_amplitude(raw_amplitude, amplitude)
    await self._set_shaking_duration(duration)

    try:
      responses = await self._send_command(
        "SHAKING ON", wait_for_terminal=False, recover_on_timeout=False
      )
    except TimeoutError as error:
      raise TecanInfiniteResponseError(
        "SHAKING ON",
        [],
        "was not received. The shaking action may have started. The reader may still be shaking",
      ) from error
    self._require_busy_response("SHAKING ON", responses)
    if len(responses) == 2:
      return
    try:
      terminal = await self._read_command_response(
        max_iterations=1, timeout=duration + 5, recover_on_timeout=False
      )
    except TimeoutError as error:
      raise TecanInfiniteResponseError(
        "SHAKING ON",
        responses,
        "started, but completion could not be confirmed; the reader may still be shaking",
      ) from error
    if terminal != ["ST"]:
      raise TecanInfiniteResponseError(
        "SHAKING ON", terminal, "did not finish with the standby response"
      )

  async def request_instrument_status(self) -> TecanInfiniteInstrumentStatus:
    """Read the reader status. Return the interpreted state and the unmodified response."""

    response = await self._request_state("QQ")
    if response in self._STATUS_STATES:
      state = self._STATUS_STATES[response]
    elif response.startswith("MSG"):
      state = "message"
    else:
      busy_match = self._BUSY_STATUS_RE.fullmatch(response)
      if busy_match is None:
        state = "unknown"
      elif busy_match.group("background"):
        state = "busy_in_background"
      else:
        state = "busy"
    return TecanInfiniteInstrumentStatus(state=state, raw=response)

  async def request_is_busy(self) -> bool:
    """Return whether the reader reports a busy state.

    Return ``False`` for a known standby or power state. Raise an error if the response does not
    identify a known busy or non-busy state.
    """

    status = await self.request_instrument_status()
    if status.state in {"busy", "busy_in_background"}:
      return True
    if status.state in {"standby", "power_down", "power_up", "parked"}:
      return False
    raise TecanInfiniteResponseError(
      "QQ", [status.raw], "did not report a known busy or non-busy state"
    )

  async def _move_plate_transport(self, destination: Literal["IN", "OUT"]) -> None:
    """Move the plate transport. Confirm the final position that the reader reports."""

    command = f"ABSOLUTE MTP,{destination}"
    try:
      responses = await self._send_command(
        command, wait_for_terminal=False, recover_on_timeout=False
      )
    except TimeoutError as error:
      raise TecanInfiniteResponseError(
        command,
        [],
        "was not received. The transport movement may have started. "
        f"The transport may not be {destination}",
      ) from error
    self._require_busy_response(command, responses)
    if len(responses) == 1:
      try:
        terminal = await self._read_command_response(
          max_iterations=1, timeout=10, recover_on_timeout=False
        )
      except TimeoutError as error:
        raise TecanInfiniteResponseError(
          command,
          responses,
          f"started, but completion could not be confirmed; the transport may not be {destination}",
        ) from error
      if terminal != ["ST"]:
        raise TecanInfiniteResponseError(
          command, terminal, "did not finish with the standby response"
        )

    applied_position = await self._request_state("?ABSOLUTE MTP,POS", recover_on_timeout=False)
    if applied_position != destination:
      raise TecanInfiniteResponseError(
        "?ABSOLUTE MTP,POS",
        [applied_position],
        f"did not confirm the requested {destination} position",
      )

  async def _request_temperature(
    self,
    reading: Literal["CURRENT", "TARGET"],
    *,
    recover_on_timeout: bool = True,
  ) -> float:
    """Read the current or target plate temperature in degrees Celsius.

    Set ``reading`` to ``CURRENT`` for the measured value or ``TARGET`` for the configured target.
    The reader reports the value in tenths of a degree Celsius. If ``recover_on_timeout`` is
    ``False``, do not reinitialize the reader after a timeout.
    """

    command = f"?TEMPERATURE PLATE,{reading}"
    response = await self._request_state(command, recover_on_timeout=recover_on_timeout)
    try:
      return int(response) / 10.0
    except ValueError as error:
      raise TecanInfiniteResponseError(
        command, [response], "did not contain a temperature in tenths of a degree Celsius"
      ) from error

  async def _request_temperature_status(
    self,
    *,
    recover_on_timeout: bool = True,
  ) -> TecanInfiniteTemperatureStatus:
    """Read the plate-temperature status.

    If ``recover_on_timeout`` is ``False``, do not reinitialize the reader after a timeout.
    """

    command = "?TEMPERATURE PLATE,STATUS"
    response = await self._request_state(command, recover_on_timeout=recover_on_timeout)
    if response not in {"ON", "OFF"}:
      raise TecanInfiniteResponseError(
        command, [response], "contained an unknown temperature status"
      )
    return cast(TecanInfiniteTemperatureStatus, response)

  @staticmethod
  def _encode_temperature(temperature: float) -> int:
    """Convert degrees Celsius to the reader value in tenths of a degree."""

    if not math.isfinite(temperature):
      raise ValueError("Temperature must be finite.")
    if not 0.0 <= temperature <= 42.0:
      raise ValueError("Temperature must be from 0 to 42 degrees Celsius.")
    raw_temperature = round(temperature * 10)
    if not math.isclose(raw_temperature / 10.0, temperature):
      raise ValueError("Temperature must use 0.1 degree Celsius increments.")
    return raw_temperature

  async def _request_integer_state(
    self, command: str, description: str, *, recover_on_timeout: bool = True
  ) -> int:
    """Read one integer-valued device state."""

    response = await self._request_state(command, recover_on_timeout=recover_on_timeout)
    try:
      return int(response)
    except ValueError as error:
      raise TecanInfiniteResponseError(
        command, [response], f"did not contain a numeric {description}"
      ) from error

  async def _set_shaking_mode(self, mode: TecanInfiniteShakingMode) -> None:
    """Set the shaking mode. Confirm the applied mode from the reader response."""

    await self._send_control_command(f"SHAKING MODE={mode}")
    applied = await self._request_state("?SHAKING MODE", recover_on_timeout=False)
    if applied != mode:
      raise TecanInfiniteResponseError(
        "?SHAKING MODE", [applied], f"did not match the requested mode {mode}"
      )

  async def _set_shaking_amplitude(self, raw_amplitude: int, amplitude: float) -> None:
    """Set the amplitude in thousandths of a millimeter. Confirm the applied value."""

    await self._send_control_command(f"SHAKING AMPLITUDE={raw_amplitude}")
    applied = await self._request_integer_state(
      "?SHAKING AMPLITUDE", "shaking amplitude", recover_on_timeout=False
    )
    if applied != raw_amplitude:
      raise TecanInfiniteResponseError(
        "?SHAKING AMPLITUDE",
        [str(applied)],
        f"did not match the requested amplitude {amplitude}",
      )

  async def _set_shaking_duration(self, duration: int) -> None:
    """Set the shaking duration. Confirm the applied value from the reader response."""

    await self._send_control_command(f"SHAKING TIME={duration}")
    applied = await self._request_integer_state(
      "?SHAKING TIME", "shaking duration", recover_on_timeout=False
    )
    if applied != duration:
      raise TecanInfiniteResponseError(
        "?SHAKING TIME", [str(applied)], f"did not match the requested duration {duration}"
      )

  async def _send_control_command(self, command: str) -> None:
    """Send a command that can change reader state. Require an ``ST`` or ``+`` response.

    Do not reinitialize the reader after a timeout. The resulting reader state can be unknown.
    """

    try:
      responses = await self._send_command(
        command, wait_for_terminal=False, recover_on_timeout=False
      )
    except TimeoutError as error:
      raise TecanInfiniteResponseError(
        command,
        [],
        "may have been accepted, but its outcome could not be confirmed",
      ) from error
    if responses not in (["ST"], ["+"]):
      raise TecanInfiniteResponseError(command, responses, "did not confirm the requested change")

  @staticmethod
  def _require_busy_response(command: str, responses: Sequence[str]) -> None:
    """Require a timed busy response for an accepted timed action."""

    if (
      not responses
      or not responses[0].startswith("BY#T")
      or len(responses) > 2
      or (len(responses) == 2 and responses[1] != "ST")
    ):
      raise TecanInfiniteResponseError(command, responses, "did not report a timed busy state")

  async def _request_state(self, command: str, *, recover_on_timeout: bool = True) -> str:
    """Send one read-only query. Require one response frame that is not a device error.

    If ``recover_on_timeout`` is ``False``, do not reinitialize the reader after a timeout.
    """

    try:
      responses = await self._send_command(
        command, wait_for_terminal=False, recover_on_timeout=recover_on_timeout
      )
    except TimeoutError as error:
      if recover_on_timeout:
        raise
      raise TecanInfiniteResponseError(
        command,
        [],
        "could not be read without reinitializing the device; its state is indeterminate",
      ) from error
    if len(responses) != 1:
      raise TecanInfiniteResponseError(command, responses, "did not contain exactly one frame")
    response = responses[0]
    if response.startswith("ERR") or response == "-":
      raise TecanInfiniteResponseError(command, responses, "reported a device error")
    return response

  async def _run_scan(
    self,
    ordered_wells: Sequence[Well],
    decoder: _MeasurementDecoder,
    mode: str,
    step_loss_commands: List[str],
    serpentine: bool,
    scan_direction: str,
  ) -> None:
    """Run the common scan loop for all measurement types.

    Args:
      ordered_wells: The wells to scan in row-major order.
      decoder: The decoder to use for parsing measurements.
      mode: The mode name for logging (e.g., "Absorbance").
      step_loss_commands: Commands to run after the scan to check for step loss.
      serpentine: Whether to use serpentine scan order.
      scan_direction: The scan direction command (e.g., "ALTUP", "UP").
    """
    self._active_step_loss_commands = step_loss_commands

    for row_index, row_wells in self._group_by_row(ordered_wells):
      start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=serpentine)
      _, y_stage = self._map_well_to_stage(row_wells[0])

      await self._send_command(f"ABSOLUTE MTP,Y={y_stage}")
      # Match the OEM one-row scan flow by explicitly pre-positioning the transport to the
      # row start before issuing SCANX. Hardware testing showed the standalone XY move alone
      # can reintroduce the first-row edge-read problem.
      await self._send_command(f"ABSOLUTE MTP,X={start_x},Y={y_stage}")
      await self._send_command(f"SCAN DIRECTION={scan_direction}")
      await self._send_command(
        f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False, read_response=False
      )
      logger.info(
        "Queued %s scan row %s (%s wells): y=%s, x=%s..%s",
        mode.lower(),
        row_index,
        count,
        y_stage,
        start_x,
        end_x,
      )
      await self._await_measurements(decoder, count, mode)
      await self._await_scan_terminal(decoder.pop_terminal())

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    flashes: int = 25,
    bandwidth: Optional[float] = None,
  ) -> List[Dict]:
    """Queue and execute an absorbance scan.

    Args:
      bandwidth: Excitation bandwidth in nm. If None, auto-selected (9 nm for >315 nm, 5 nm
        otherwise).
    """

    if not 230 <= wavelength <= 1_000:
      raise ValueError("Absorbance wavelength must be between 230 nm and 1000 nm.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=True)
    decoder = _AbsorbanceRunDecoder(len(scan_wells))

    await self._begin_run()
    try:
      await self._configure_absorbance(wavelength, flashes=flashes, bandwidth=bandwidth)
      await self._run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Absorbance",
        step_loss_commands=["CHECK MTP.STEPLOSS", "CHECK ABS.STEPLOSS"],
        serpentine=True,
        scan_direction="ALTUP",
      )

      self._drain_pending_bin_events(decoder)
      if len(decoder.measurements) != len(scan_wells):
        raise RuntimeError("Absorbance decoder did not complete scan.")
      intensities: List[float] = []
      cal = decoder.calibration
      if cal is None:
        raise RuntimeError("ABS calibration packet not seen; cannot compute calibrated OD.")
      for meas in decoder.measurements:
        items = meas.items or [(meas.sample, meas.reference)]
        od = _absorbance_od_calibrated(cal, items)
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
      await self._end_run()

  async def _clear_mode_settings(self, excitation: bool = False, emission: bool = False) -> None:
    """Clear mode settings before configuring a new scan."""
    if excitation:
      await self._send_command("EXCITATION CLEAR", allow_timeout=True)
    if emission:
      await self._send_command("EMISSION CLEAR", allow_timeout=True)
    await self._send_command("TIME CLEAR", allow_timeout=True)
    await self._send_command("GAIN CLEAR", allow_timeout=True)
    await self._send_command("READS CLEAR", allow_timeout=True)
    await self._send_command("POSITION CLEAR", allow_timeout=True)
    await self._send_command("MIRROR CLEAR", allow_timeout=True)

  async def _configure_absorbance(
    self,
    wavelength_nm: int,
    *,
    flashes: int,
    bandwidth: Optional[float] = None,
  ) -> None:
    wl_decitenth = int(round(wavelength_nm * 10))
    bw = bandwidth if bandwidth is not None else self._auto_bandwidth(wavelength_nm)
    bw_decitenth = int(round(bw * 10))
    reads_number = max(1, flashes)

    await self._send_command("MODE ABS")
    await self._clear_mode_settings(excitation=True)
    await self._send_command(
      f"EXCITATION 0,ABS,{wl_decitenth},{bw_decitenth},0", allow_timeout=True
    )
    await self._send_command(
      f"EXCITATION 1,ABS,{wl_decitenth},{bw_decitenth},0", allow_timeout=True
    )
    await self._send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
    await self._send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self._send_command("TIME 0,READDELAY=0", allow_timeout=True)
    await self._send_command("TIME 1,READDELAY=0", allow_timeout=True)
    await self._send_command("SCAN DIRECTION=ALTUP", allow_timeout=True)
    await self._send_command("#RATIO LABELS", allow_timeout=True)
    await self._send_command(
      f"BEAM DIAMETER={self._capability_numeric('ABS', '#BEAM DIAMETER', 700)}", allow_timeout=True
    )
    await self._send_command("RATIO LABELS=1", allow_timeout=True)
    await self._send_command("PREPARE REF", allow_timeout=True, read_response=False)

  def _auto_bandwidth(self, wavelength_nm: int) -> float:
    """Return bandwidth in nm based on Infinite M specification."""

    return 9.0 if wavelength_nm > 315 else 5.0

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float = 20.0,
    flashes: int = 25,
    integration_us: int = 20,
    gain: int = 100,
    excitation_bandwidth: int = 50,
    emission_bandwidth: int = 200,
    lag_us: int = 0,
  ) -> List[Dict]:
    """Queue and execute a fluorescence scan.

    Args:
      gain: PMT gain value (0-255).
      excitation_bandwidth: Excitation filter bandwidth in deci-tenths of nm.
      emission_bandwidth: Emission filter bandwidth in deci-tenths of nm.
      lag_us: Lag time in microseconds between excitation and measurement.
    """

    if not 230 <= excitation_wavelength <= 850:
      raise ValueError("Excitation wavelength must be between 230 nm and 850 nm.")
    if not 230 <= emission_wavelength <= 850:
      raise ValueError("Emission wavelength must be between 230 nm and 850 nm.")
    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for fluorescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=True)

    await self._begin_run()
    try:
      await self._configure_fluorescence(
        excitation_wavelength,
        emission_wavelength,
        focal_height,
        flashes=flashes,
        integration_us=integration_us,
        gain=gain,
        excitation_bandwidth=excitation_bandwidth,
        emission_bandwidth=emission_bandwidth,
        lag_us=lag_us,
      )
      decoder = _FluorescenceRunDecoder(len(scan_wells))

      await self._run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Fluorescence",
        step_loss_commands=[
          "CHECK MTP.STEPLOSS",
          "CHECK FI.TOP.STEPLOSS",
          "CHECK FI.STEPLOSS.Z",
        ],
        serpentine=True,
        scan_direction="UP",
      )

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
      await self._end_run()

  async def _configure_fluorescence(
    self,
    excitation_nm: int,
    emission_nm: int,
    focal_height: float,
    *,
    flashes: int,
    integration_us: int,
    gain: int,
    excitation_bandwidth: int,
    emission_bandwidth: int,
    lag_us: int,
  ) -> None:
    ex_decitenth = int(round(excitation_nm * 10))
    em_decitenth = int(round(emission_nm * 10))
    reads_number = max(1, flashes)
    beam_diameter = self._capability_numeric("FI.TOP", "#BEAM DIAMETER", 3000)
    z_position = int(round(focal_height * self.counts_per_mm_z))

    # UI issues the entire FI configuration twice before PREPARE REF.
    for _ in range(2):
      await self._send_command("MODE FI.TOP", allow_timeout=True)
      await self._clear_mode_settings(excitation=True, emission=True)
      await self._send_command(
        f"EXCITATION 0,FI,{ex_decitenth},{excitation_bandwidth},0", allow_timeout=True
      )
      await self._send_command(
        f"EMISSION 0,FI,{em_decitenth},{emission_bandwidth},0", allow_timeout=True
      )
      await self._send_command(f"TIME 0,INTEGRATION={integration_us}", allow_timeout=True)
      await self._send_command(f"TIME 0,LAG={lag_us}", allow_timeout=True)
      await self._send_command("TIME 0,READDELAY=0", allow_timeout=True)
      await self._send_command(f"GAIN 0,VALUE={gain}", allow_timeout=True)
      await self._send_command(f"POSITION 0,Z={z_position}", allow_timeout=True)
      await self._send_command(f"BEAM DIAMETER={beam_diameter}", allow_timeout=True)
      await self._send_command("SCAN DIRECTION=UP", allow_timeout=True)
      await self._send_command("RATIO LABELS=1", allow_timeout=True)
      await self._send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
      await self._send_command(
        f"EXCITATION 1,FI,{ex_decitenth},{excitation_bandwidth},0", allow_timeout=True
      )
      await self._send_command(
        f"EMISSION 1,FI,{em_decitenth},{emission_bandwidth},0", allow_timeout=True
      )
      await self._send_command(f"TIME 1,INTEGRATION={integration_us}", allow_timeout=True)
      await self._send_command(f"TIME 1,LAG={lag_us}", allow_timeout=True)
      await self._send_command("TIME 1,READDELAY=0", allow_timeout=True)
      await self._send_command(f"GAIN 1,VALUE={gain}", allow_timeout=True)
      await self._send_command(f"POSITION 1,Z={z_position}", allow_timeout=True)
      await self._send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self._send_command("PREPARE REF", allow_timeout=True, read_response=False)

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float = 20.0,
    flashes: int = 25,
    dark_integration_us: int = 3_000_000,
    meas_integration_us: int = 1_000_000,
  ) -> List[Dict]:
    """Queue and execute a luminescence scan."""

    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for luminescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=False)

    dark_integration = dark_integration_us
    meas_integration = meas_integration_us

    await self._begin_run()
    try:
      await self._configure_luminescence(
        dark_integration, meas_integration, focal_height, flashes=flashes
      )

      decoder = _LuminescenceRunDecoder(
        len(scan_wells),
        dark_integration_s=_integration_microseconds_to_seconds(dark_integration),
        meas_integration_s=_integration_microseconds_to_seconds(meas_integration),
      )

      await self._run_scan(
        ordered_wells=ordered_wells,
        decoder=decoder,
        mode="Luminescence",
        step_loss_commands=["CHECK MTP.STEPLOSS", "CHECK LUM.STEPLOSS"],
        serpentine=False,
        scan_direction="UP",
      )

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
      await self._end_run()

  async def _await_measurements(
    self, decoder: "_MeasurementDecoder", row_count: int, mode: str
  ) -> None:
    target = decoder.count + row_count
    start_count = decoder.count
    self._drain_pending_bin_events(decoder)
    start = time.monotonic()
    reads = 0
    while decoder.count < target and (time.monotonic() - start) < self._max_row_wait_s:
      chunk = await self._read_packet(self._read_chunk_size)
      if not chunk:
        raise RuntimeError(f"{mode} read returned empty chunk; transport may not support reads.")
      decoder.feed(chunk)
      reads += 1
    if decoder.count < target:
      got = decoder.count - start_count
      raise RuntimeError(
        f"Timed out while parsing {mode.lower()} results "
        f"(decoded {got}/{row_count} measurements in {time.monotonic() - start:.1f}s, {reads} reads)."
      )

  def _drain_pending_bin_events(self, decoder: "_MeasurementDecoder") -> None:
    if not self._pending_bin_events:
      return
    for payload_len, blob in self._pending_bin_events:
      decoder.feed_bin(payload_len, blob)
    self._pending_bin_events.clear()

  async def _await_scan_terminal(self, saw_terminal: bool) -> None:
    if saw_terminal:
      return
    await self._read_command_response()

  async def _configure_luminescence(
    self,
    dark_integration: int,
    meas_integration: int,
    focal_height: float,
    *,
    flashes: int,
  ) -> None:
    await self._send_command("MODE LUM")
    # Pre-flight safety checks observed in captures (queries omitted).
    await self._send_command("CHECK LUM.FIBER")
    await self._send_command("CHECK LUM.LID")
    await self._send_command("CHECK LUM.STEPLOSS")
    await self._send_command("MODE LUM")
    reads_number = max(1, flashes)
    z_position = int(round(focal_height * self.counts_per_mm_z))
    await self._clear_mode_settings(emission=True)
    await self._send_command(f"POSITION LUM,Z={z_position}", allow_timeout=True)
    await self._send_command(f"TIME 0,INTEGRATION={dark_integration}", allow_timeout=True)
    await self._send_command(f"READS 0,NUMBER={reads_number}", allow_timeout=True)
    await self._send_command("SCAN DIRECTION=UP", allow_timeout=True)
    await self._send_command("RATIO LABELS=1", allow_timeout=True)
    await self._send_command("EMISSION 1,EMPTY,0,0,0", allow_timeout=True)
    await self._send_command(f"TIME 1,INTEGRATION={meas_integration}", allow_timeout=True)
    await self._send_command("TIME 1,READDELAY=0", allow_timeout=True)
    await self._send_command(f"READS 1,NUMBER={reads_number}", allow_timeout=True)
    await self._send_command("#EMISSION ATTENUATION", allow_timeout=True)
    await self._send_command("PREPARE REF", allow_timeout=True, read_response=False)

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
    stage_x = int(round(center.x * self.counts_per_mm_x))
    parent_plate = well.parent
    if parent_plate is None or not isinstance(parent_plate, Plate):
      raise ValueError("Well is not assigned to a plate; cannot derive stage coordinates.")
    plate_height_mm = parent_plate.get_size_y()
    stage_y = int(round((plate_height_mm - center.y) * self.counts_per_mm_y))
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
    try:
      await self._send_command("QQ")
    except TimeoutError:
      logger.warning("QQ produced no response; continuing with initialization.")
    await self._send_command("INIT FORCE")

  async def _begin_run(self) -> None:
    self._reset_stream_state()
    await self._send_command("KEYLOCK ON")
    self._run_active = True

  def _reset_stream_state(self) -> None:
    self._pending_bin_events.clear()
    self._parser = _StreamParser(allow_bare_ascii=True)

  async def _read_packet(
    self, size: int, timeout: Optional[int] = None, *, recover_on_timeout: bool = True
  ) -> bytes:
    try:
      if timeout is None:
        data = await self.io.read(size=size)
      else:
        data = await self.io.read(timeout=timeout, size=size)
    except TimeoutError:
      if recover_on_timeout:
        await self._recover_transport()
      raise
    return data

  async def _recover_transport(self) -> None:
    try:
      await self.io.stop()
      await asyncio.sleep(0.2)
      await self.io.setup()
    except Exception:
      logger.warning("Transport recovery failed.", exc_info=True)
      return
    self._mode_capabilities.clear()
    self._reset_stream_state()
    await self._initialize_device()

  async def _end_run(self) -> None:
    try:
      await self._send_command("TERMINATE", allow_timeout=True)
      for cmd in self._active_step_loss_commands:
        await self._send_command(cmd, allow_timeout=True)
      await self._send_command("KEYLOCK OFF", allow_timeout=True)
      await self._send_command("ABSOLUTE MTP,IN", allow_timeout=True)
    finally:
      self._run_active = False
      self._active_step_loss_commands = []

  async def _cleanup_protocol(self) -> None:
    async def send_cleanup_cmd(cmd: str) -> None:
      try:
        await self._send_command(cmd, allow_timeout=True, read_response=False)
      except Exception:
        logger.warning("Cleanup command failed: %s", cmd)

    if self._run_active or self._active_step_loss_commands:
      await send_cleanup_cmd("TERMINATE")
      for cmd in self._active_step_loss_commands:
        await send_cleanup_cmd(cmd)
    await send_cleanup_cmd("KEYLOCK OFF")
    await send_cleanup_cmd("ABSOLUTE MTP,IN")
    self._run_active = False
    self._active_step_loss_commands = []

  async def _query_mode_capabilities(self, mode: str) -> None:
    commands = self._MODE_CAPABILITY_COMMANDS.get(mode)
    if not commands:
      return
    try:
      await self._send_command(f"MODE {mode}")
    except TimeoutError:
      logger.warning("Capability MODE %s timed out; continuing without mode capabilities.", mode)
      return
    collected: Dict[str, str] = {}
    for cmd in commands:
      try:
        frames = await self._send_command(cmd)
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
  def _frame_command(command: str) -> bytes:
    """Return a framed command with length/checksum trailer."""

    payload = command.encode("ascii")
    xor = 0
    for byte in payload:
      xor ^= byte
    checksum = (xor ^ 0x01) & 0xFF
    length = len(payload) & 0xFF
    return b"\x02" + payload + b"\x03\x00\x00" + bytes([length, checksum]) + b"\x0d"

  async def _send_command(
    self,
    command: str,
    wait_for_terminal: bool = True,
    allow_timeout: bool = False,
    read_response: bool = True,
    recover_on_timeout: bool = True,
  ) -> List[str]:
    logger.debug("[tecan] >> %s", command)
    framed = self._frame_command(command)
    await self.io.write(framed)
    if not read_response:
      return []
    if command.startswith(("#", "?")):
      try:
        return await self._read_command_response(
          require_terminal=False, recover_on_timeout=recover_on_timeout
        )
      except TimeoutError:
        if allow_timeout:
          logger.warning("Timeout waiting for response to %s", command)
          return []
        raise
    try:
      frames = await self._read_command_response(
        require_terminal=wait_for_terminal, recover_on_timeout=recover_on_timeout
      )
    except TimeoutError:
      if allow_timeout:
        logger.warning("Timeout waiting for response to %s", command)
        return []
      raise
    for pkt in frames:
      logger.debug("[tecan] << %s", pkt)
    return frames

  async def _drain(self, attempts: int = 4) -> None:
    """Read and discard a few packets to clear the stream."""
    for _ in range(attempts):
      data = await self._read_packet(128)
      if not data:
        break

  async def _read_command_response(
    self,
    max_iterations: int = 8,
    require_terminal: bool = True,
    timeout: Optional[int] = None,
    *,
    recover_on_timeout: bool = True,
  ) -> List[str]:
    """Read response frames and cache any binary payloads that arrive."""
    frames: List[str] = []
    saw_terminal = False
    for _ in range(max_iterations):
      chunk = await self._read_packet(128, timeout=timeout, recover_on_timeout=recover_on_timeout)
      if not chunk:
        break
      for event in self._parser.feed(chunk):
        if event.text is not None:
          frames.append(event.text)
          if self._is_terminal_frame(event.text):
            saw_terminal = True
        elif event.payload_len is not None and event.blob is not None:
          self._pending_bin_events.append((event.payload_len, event.blob))
      if not require_terminal and frames and not self._parser.has_pending_bin():
        break
      if require_terminal and saw_terminal and not self._parser.has_pending_bin():
        break
    if require_terminal and not saw_terminal and recover_on_timeout:
      # best effort: drain once more so pending ST doesn't leak into next command
      await self._drain(1)
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
    self._calibration: Optional[_AbsorbanceCalibration] = None

  @property
  def count(self) -> int:
    return len(self.measurements)

  @property
  def calibration(self) -> Optional[_AbsorbanceCalibration]:
    """Return the absorbance calibration data, if available."""
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
    """Return decoded fluorescence intensities."""
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


__all__ = [
  "ExperimentalTecanInfinite200ProBackend",
  "TecanInfiniteInstrumentState",
  "TecanInfiniteInstrumentStatus",
  "TecanInfinitePlatePosition",
  "TecanInfinitePlateSensorState",
  "TecanInfiniteResponseError",
  "TecanInfiniteShakingMode",
  "TecanInfiniteTemperatureStatus",
]
