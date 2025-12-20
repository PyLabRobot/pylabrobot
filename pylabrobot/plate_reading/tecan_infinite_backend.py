"""Tecan Infinite 200 PRO backend.

This backend targets the Infinite "M" series (e.g., Infinite 200 PRO).  The
"F" series uses a different optical path and is not covered here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from pylabrobot.io.usb import USB
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)


class InfiniteTransport(Protocol):
  """Minimal transport required by the backend.

  Implementations are expected to wrap PyUSB/libusbK.
  """

  async def open(self) -> None:
    ...

  async def close(self) -> None:
    ...

  async def write(self, data: bytes) -> None:
    ...

  async def read(self, size: int) -> bytes:
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
  flashes: int = 25
  counts_per_mm_x: float = 1_000
  counts_per_mm_y: float = 1_000


TecanScanConfig = InfiniteScanConfig


def _be16_words(payload: bytes) -> List[int]:
  return [int.from_bytes(payload[i : i + 2], "big") for i in range(0, len(payload), 2)]


StagePosition = Tuple[int, int]


def _consume_leading_ascii_frame(buffer: bytearray) -> Tuple[bool, Optional[str]]:
  """Remove a leading STX...ETX ASCII frame if present."""

  if not buffer or buffer[0] != 0x02:
    return False, None
  end = buffer.find(b"\x03", 1)
  if end == -1:
    return False, None
  text = buffer[1:end].decode("ascii", "ignore")
  del buffer[: end + 2]
  if buffer and buffer[0] == 0x0D:
    del buffer[0]
  return True, text


def _consume_status_frame(buffer: bytearray, length: int) -> bool:
  """Drop a leading ESC-prefixed status frame if present."""

  if len(buffer) >= length and buffer[0] == 0x1B:
    del buffer[:length]
    return True
  return False


class _MeasurementDecoder(ABC):
  """Shared incremental decoder for Infinite measurement streams."""

  STATUS_FRAME_LEN: Optional[int] = None

  def __init__(self, expected: int) -> None:
    self.expected = expected
    self._buffer: bytearray = bytearray()
    self._terminal_seen = False

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
    self._buffer.extend(chunk)
    progressed = True
    while progressed:
      progressed = False
      consumed, text = _consume_leading_ascii_frame(self._buffer)
      if consumed:
        if text == "ST":
          self._terminal_seen = True
        progressed = True
        continue
      if not self.done and self._consume_measurement():
        progressed = True
        continue
      if self.STATUS_FRAME_LEN and _consume_status_frame(self._buffer, self.STATUS_FRAME_LEN):
        progressed = True
        continue
      if self.done or not self._buffer:
        break
      progressed = self._discard_byte()

  @abstractmethod
  def _consume_measurement(self) -> bool:
    """Attempt to consume a measurement frame from the buffer."""

  def _discard_byte(self) -> bool:
    if self._buffer:
      del self._buffer[0]
      return True
    return False


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
      "#BEAM DIAMETER",
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
      "#BEAM DIAMETER",
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
      "#BEAM DIAMETER",
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
  ) -> None:
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
      await self._transport.close()
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

    await self._begin_run()
    try:
      wl_decitenth = int(round(wavelength * 10))
      decoder = _AbsorbanceRunDecoder(len(scan_wells), wl_decitenth)
      await self._configure_absorbance(wavelength)

      for row_index, row_wells in self._group_by_row(ordered_wells):
        start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=True)
        _, y_stage = self._map_well_to_stage(row_wells[0])

        await self._send_ascii(f"ABSOLUTE MTP,Y={y_stage}")
        await self._send_ascii("SCAN DIRECTION=ALTUP")
        await self._send_ascii(f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False)
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
      intensities = [
        self._calculate_absorbance_od(meas.sample, meas.reference) for meas in decoder.measurements
      ]
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
      await self._end_run(["CHECK MTP.STEPLOSS", "CHECK ABS.STEPLOSS"])

  async def _configure_absorbance(self, wavelength_nm: int) -> None:
    wl_decitenth = int(round(wavelength_nm * 10))
    bw_decitenth = int(round(self._auto_bandwidth(wavelength_nm) * 10))
    reads_number = 1

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
      await self._send_ascii(cmd)

  def _auto_bandwidth(self, wavelength_nm: int) -> float:
    """Return bandwidth in nm based on Infinite M specification."""

    return 9.0 if wavelength_nm > 315 else 5.0

  @staticmethod
  def _calculate_absorbance_od(
    sample: int,
    reference: int,
  ) -> float:
    """Return log10(reference / sample) with guard rails around zero."""

    safe_sample = max(sample, 1)
    safe_reference = max(reference, 1)
    return float(math.log10(safe_reference / safe_sample))

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
        await self._send_ascii(f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False)
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
      await self._end_run(["CHECK MTP.STEPLOSS", "CHECK FI.TOP.STEPLOSS", "CHECK FI.STEPLOSS.Z"])

  async def _configure_fluorescence(self, excitation_nm: int, emission_nm: int) -> None:
    ex_decitenth = int(round(excitation_nm * 10))
    em_decitenth = int(round(emission_nm * 10))
    self._current_fluorescence_excitation = ex_decitenth
    self._current_fluorescence_emission = em_decitenth
    reads_number = 1
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
        await self._send_ascii(cmd)
      for cmd in configure_cmds:
        await self._send_ascii(cmd)
    await self._send_ascii("PREPARE REF")

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float,
  ) -> List[Dict]:
    """Queue and execute a luminescence scan."""

    logger.warning("Luminescence path is experimental; decoding is not yet validated.")
    if focal_height < 0:
      raise ValueError("Focal height must be non-negative for luminescence scans.")

    ordered_wells = wells if wells else plate.get_all_items()
    scan_wells = self._scan_visit_order(ordered_wells, serpentine=False)
    await self._begin_run()
    try:
      await self._configure_luminescence()
      decoder = _LuminescenceRunDecoder(len(scan_wells))

      for row_index, row_wells in self._group_by_row(ordered_wells):
        start_x, end_x, count = self._scan_range(row_index, row_wells, serpentine=False)
        _, y_stage = self._map_well_to_stage(row_wells[0])

        await self._send_ascii(f"ABSOLUTE MTP,Y={y_stage}")
        await self._send_ascii("SCAN DIRECTION=UP")
        await self._send_ascii(f"SCANX {start_x},{end_x},{count}", wait_for_terminal=False)
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
      await self._end_run(["CHECK MTP.STEPLOSS", "CHECK LUM.STEPLOSS"])

  async def _await_measurements(
    self, decoder: "_MeasurementDecoder", row_count: int, mode: str
  ) -> None:
    target = decoder.count + row_count
    iterations = 0
    while decoder.count < target and iterations < self._max_read_iterations:
      chunk = await self._transport.read(self._read_chunk_size)
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
    commands = [
      "READS CLEAR",
      "EMISSION CLEAR",
      "TIME CLEAR",
      "GAIN CLEAR",
      "POSITION CLEAR",
      "MIRROR CLEAR",
      "POSITION LUM,Z=14620",
      "TIME 0,INTEGRATION=3000000",
      "SCAN DIRECTION=UP",
      "RATIO LABELS=1",
      "EMISSION 1,EMPTY,0,0,0",
      "TIME 1,INTEGRATION=1000000",
      "TIME 1,READDELAY=0",
      "#EMISSION ATTENUATION",
      "PREPARE REF",
    ]
    for cmd in commands:
      await self._send_ascii(cmd)

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
    await self._send_ascii("KEYLOCK ON")

  async def _end_run(self, step_loss_commands: Sequence[str]) -> None:
    await self._send_ascii("TERMINATE")
    for cmd in step_loss_commands:
      await self._send_ascii(cmd)
    await self._send_ascii("KEYLOCK OFF")
    await self._send_ascii("ABSOLUTE MTP,IN")

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

  async def _send_ascii(self, command: str, wait_for_terminal: bool = True) -> List[str]:
    logger.debug("[tecan] >> %s", command)
    framed = self._frame_ascii_command(command)
    await self._transport.write(framed)
    if command.startswith(("#", "?")):
      frames = await self._read_ascii_response(require_terminal=False)
      return frames
    frames = await self._read_ascii_response(require_terminal=wait_for_terminal)
    for pkt in frames:
      logger.debug("[tecan] << %s", pkt)
    return frames

  async def _drain_ascii(self, attempts: int = 4) -> None:
    for _ in range(attempts):
      data = await self._transport.read(128)
      if not data:
        break

  async def _read_ascii_response(
    self, max_iterations: int = 8, require_terminal: bool = True
  ) -> List[str]:
    buffer = bytearray()
    frames: List[str] = []
    saw_terminal = False
    for _ in range(max_iterations):
      chunk = await self._transport.read(128)
      if not chunk:
        break
      buffer.extend(chunk)
      decoded = self._decode_ascii_frames(buffer)
      if decoded:
        frames.extend(decoded)
        if not require_terminal:
          break
        if any(self._is_terminal_frame(text) for text in decoded):
          saw_terminal = True
          break
        continue
      if buffer and all(32 <= b <= 126 for b in buffer):
        text = ""
        try:
          text = buffer.decode("ascii", "ignore")
          frames.append(text)
        except Exception:
          pass
        buffer.clear()
        if self._is_terminal_frame(text):
          saw_terminal = True
          break
        continue
    if require_terminal and not saw_terminal:
      # best effort: drain once more so pending ST doesn't leak into next command
      await self._drain_ascii(1)
    return frames

  @staticmethod
  def _is_terminal_frame(text: str) -> bool:
    return text in {"ST", "+", "-"} or text.startswith("BY#T")

  @staticmethod
  def _decode_ascii_frames(data: bytearray) -> List[str]:
    frames: List[str] = []
    while True:
      try:
        stx = data.index(0x02)
      except ValueError:
        data.clear()
        break
      if stx > 0:
        del data[:stx]
      try:
        etx = data.index(0x03, 1)
      except ValueError:
        break
      trailer_len = 4 if len(data) >= etx + 5 else 0
      frame_end = etx + 1 + trailer_len
      if len(data) < frame_end:
        break
      payload = data[1:etx]
      try:
        frames.append(payload.decode("ascii", "ignore"))
      except Exception:
        frames.append(payload.hex())
      del data[:frame_end]
      if data and data[0] == 0x0D:
        del data[0]
    return frames


@dataclass
class _AbsorbanceMeasurement:
  sample: int
  reference: int


class _AbsorbanceRunDecoder(_MeasurementDecoder):
  """Incrementally decode absorbance measurement frames."""

  STATUS_FRAME_LEN = 31
  _MEAS_LEN = 18

  def __init__(self, expected: int, wavelength_decitenth: int, skip_initial: int = 0) -> None:
    super().__init__(expected)
    self._wavelength = wavelength_decitenth
    self.measurements: List[_AbsorbanceMeasurement] = []
    self._skip_initial = max(0, skip_initial)

  @property
  def count(self) -> int:
    return len(self.measurements)

  def _consume_measurement(self) -> bool:
    frame = self._find_measurement_frame()
    if frame is None:
      return False
    offset, length = frame
    if offset:
      del self._buffer[:offset]
    payload = bytes(self._buffer[:length])
    del self._buffer[:length]
    self._handle_measurement(payload)
    return True

  def _handle_measurement(self, payload: bytes) -> None:
    words = _be16_words(payload)
    if len(words) != 9:
      return
    if not self._words_match_measurement(words):
      return
    meas = _AbsorbanceMeasurement(sample=words[5], reference=words[6])
    if self._skip_initial > 0:
      self._skip_initial -= 1
      return
    self.measurements.append(meas)

  def _find_measurement_frame(self) -> Optional[Tuple[int, int]]:
    limit = len(self._buffer) - self._MEAS_LEN
    for offset in range(0, limit + 1, 2):
      chunk = self._buffer[offset : offset + self._MEAS_LEN]
      words = _be16_words(chunk)
      if len(words) == 9 and self._words_match_measurement(words):
        return offset, self._MEAS_LEN
    return None

  def _words_match_measurement(self, words: List[int]) -> bool:
    if len(words) != 9:
      return False
    if words[0] != 1 or words[2] != 0:
      return False
    if abs(words[1] - self._wavelength) > 1:
      return False
    return True


class _FluorescenceRunDecoder(_MeasurementDecoder):
  """Incrementally decode fluorescence measurement frames from measurement tails."""

  STATUS_FRAME_LEN = 31
  _MEAS_LEN = 20

  def __init__(
    self, expected_wells: int, excitation_decitenth: int, emission_decitenth: Optional[int]
  ) -> None:
    super().__init__(expected_wells)
    self._excitation = excitation_decitenth
    self._emission = emission_decitenth
    self._intensities: List[int] = []

  @property
  def count(self) -> int:
    return len(self._intensities)

  @property
  def intensities(self) -> List[int]:
    return self._intensities

  def _consume_measurement(self) -> bool:
    frame = self._find_measurement_frame()
    if frame:
      offset, length = frame
      if offset:
        del self._buffer[:offset]
      tail = bytes(self._buffer[:length])
      del self._buffer[:length]
      self._handle_measurement_tail(tail)
      return True
    calib_len = self._calibration_frame_len()
    if calib_len:
      del self._buffer[:calib_len]
      return True
    return False

  def _find_measurement_frame(self) -> Optional[Tuple[int, int]]:
    limit = len(self._buffer) - self._MEAS_LEN
    for offset in range(0, limit + 1, 2):
      chunk = self._buffer[offset : offset + self._MEAS_LEN]
      words = _be16_words(chunk)
      if len(words) == 10 and self._words_match_measurement(words):
        return offset, self._MEAS_LEN
    return None

  def _calibration_frame_len(self) -> Optional[int]:
    return None

  def _handle_measurement_tail(self, tail: bytes) -> None:
    words = _be16_words(tail)
    intensity = None
    if len(words) == 10 and self._words_match_measurement(words):
      intensity = words[6]
    if intensity is not None:
      self._intensities.append(intensity)

  def _words_match_measurement(self, words: List[int]) -> bool:
    if not words:
      return False
    excit = words[1]
    emiss = words[2]
    if words[0] != 1:
      return False
    if abs(excit - self._excitation) > 1:
      return False
    if self._emission is not None and abs(emiss - self._emission) > 1:
      return False
    return True

  def _discard_byte(self) -> bool:
    if self._buffer:
      del self._buffer[0]
      return True
    return False


@dataclass
class _LuminescenceMeasurement:
  raw_tail: int
  intensity: int
  words: List[int]


class _LuminescenceRunDecoder(_MeasurementDecoder):
  """Incrementally decode luminescence measurement frames."""

  FRAME_LEN = 45
  _MEAS_LEN = 18

  def __init__(self, expected: int) -> None:
    super().__init__(expected)
    self.measurements: List[_LuminescenceMeasurement] = []

  @property
  def count(self) -> int:
    return len(self.measurements)

  def _consume_measurement(self) -> bool:
    frame = self._find_measurement_frame()
    if frame:
      offset, length = frame
      if offset:
        del self._buffer[:offset]
      payload = bytes(self._buffer[:length])
      del self._buffer[:length]
      self._handle_measurement(payload)
      return True
    return False

  def _discard_byte(self) -> bool:
    if self._buffer and self._buffer[0] not in (0x02, 0x1B):
      del self._buffer[0]
      return True
    return False

  def _find_measurement_frame(self) -> Optional[Tuple[int, int]]:
    limit = len(self._buffer) - self._MEAS_LEN
    for offset in range(0, limit + 1, 2):
      chunk = self._buffer[offset : offset + self._MEAS_LEN]
      words = _be16_words(chunk)
      if len(words) == 9 and self._words_match_measurement(words):
        return offset, self._MEAS_LEN
    return None

  def _handle_measurement(self, payload: bytes) -> None:
    words = _be16_words(payload)
    if len(words) != 9:
      return
    if not self._words_match_measurement(words):
      return
    raw_tail = payload[-1] if payload else 0
    # Unconfirmed: using words[6] as luminescence intensity; not validated against OEM exports due to lack of proper glowing sample.
    intensity = words[6]
    self.measurements.append(
      _LuminescenceMeasurement(
        raw_tail=raw_tail,
        intensity=intensity,
        words=words,
      )
    )

  def _words_match_measurement(self, words: List[int]) -> bool:
    if len(words) != 9:
      return False
    if words[0] != 1:
      return False
    if words[2] != 0:
      return False
    return True


__all__ = [
  "TecanInfinite200ProBackend",
  "InfiniteScanConfig",
  "PyUSBInfiniteTransport",
]
