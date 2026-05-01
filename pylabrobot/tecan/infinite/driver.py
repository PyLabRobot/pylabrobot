"""Tecan Infinite 200 PRO driver.

Owns the USB connection, connection lifecycle, device-level operations
(initialize, tray control, keylock), shared scan orchestration, and
well-to-stage geometry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Sequence, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.io.usb import USB
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

from .protocol import (
  _MeasurementDecoder,
  _StreamParser,
  StagePosition,
  frame_command,
  is_terminal_frame,
)

logger = logging.getLogger(__name__)


class TecanInfiniteDriver(Driver):
  """USB driver for the Tecan Infinite 200 PRO plate reader.

  Owns the USB connection, low-level command protocol, device-level operations
  (tray open/close, initialization), shared scan orchestration, and well-to-stage
  geometry.
  """

  VENDOR_ID = 0x0C47
  PRODUCT_ID = 0x8007

  _MODE_CAPABILITY_COMMANDS: Dict[str, List[str]] = {
    "ABS": ["#BEAM DIAMETER"],
    "FI.TOP": [],
    "FI.BOTTOM": [],
    "LUM": [],
  }

  def __init__(
    self,
    counts_per_mm_x: float = 1_000,
    counts_per_mm_y: float = 1_000,
    counts_per_mm_z: float = 1_000,
    io: Optional[USB] = None,
  ) -> None:
    """
    Args:
      counts_per_mm_x: Stage counts per mm in X.
      counts_per_mm_y: Stage counts per mm in Y.
      counts_per_mm_z: Stage counts per mm in Z.
      io: Optional USB I/O instance (for test injection).
    """
    super().__init__()
    self.io = io or USB(
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

  # -- lifecycle --

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
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

  # -- device-level operations --

  async def open_tray(self) -> None:
    """Open the reader drawer."""
    await self.send_command("ABSOLUTE MTP,OUT")
    await self.send_command("BY#T5000")

  async def close_tray(self) -> None:
    """Close the reader drawer."""
    await self.send_command("ABSOLUTE MTP,IN")
    await self.send_command("BY#T5000")

  # -- generic I/O --

  async def send_command(
    self,
    command: str,
    wait_for_terminal: bool = True,
    allow_timeout: bool = False,
    read_response: bool = True,
  ) -> List[str]:
    """Send a framed ASCII command and read response frames."""
    logger.debug("[tecan] >> %s", command)
    framed = frame_command(command)
    await self.io.write(framed)
    if not read_response:
      return []
    if command.startswith(("#", "?")):
      try:
        return await self._read_command_response(require_terminal=False)
      except TimeoutError:
        if allow_timeout:
          logger.warning("Timeout waiting for response to %s", command)
          return []
        raise
    try:
      frames = await self._read_command_response(require_terminal=wait_for_terminal)
    except TimeoutError:
      if allow_timeout:
        logger.warning("Timeout waiting for response to %s", command)
        return []
      raise
    for pkt in frames:
      logger.debug("[tecan] << %s", pkt)
    return frames

  async def read_packet(self, size: int) -> bytes:
    """Read raw bytes from the USB transport."""
    try:
      data = await self.io.read(size=size)
    except TimeoutError:
      await self._recover_transport()
      raise
    return data

  # -- scan orchestration --

  async def begin_run(self) -> None:
    """Begin a measurement run (KEYLOCK ON, reset stream state)."""
    self._reset_stream_state()
    await self.send_command("KEYLOCK ON")
    self._run_active = True

  async def end_run(self) -> None:
    """End a measurement run (TERMINATE, step loss checks, KEYLOCK OFF, MTP IN)."""
    try:
      await self.send_command("TERMINATE", allow_timeout=True)
      for cmd in self._active_step_loss_commands:
        await self.send_command(cmd, allow_timeout=True)
      await self.send_command("KEYLOCK OFF", allow_timeout=True)
      await self.send_command("ABSOLUTE MTP,IN", allow_timeout=True)
    finally:
      self._run_active = False
      self._active_step_loss_commands = []

  async def run_scan(
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

    for row_index, row_wells in self.group_by_row(ordered_wells):
      start_x, end_x, count = self.scan_range(row_index, row_wells, serpentine=serpentine)
      _, y_stage = self.map_well_to_stage(row_wells[0])

      await self.send_command(f"ABSOLUTE MTP,Y={y_stage}")
      await self.send_command(f"ABSOLUTE MTP,X={start_x},Y={y_stage}")
      await self.send_command(f"SCAN DIRECTION={scan_direction}")
      await self.send_command(
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

  # -- mode capability queries --

  async def _query_mode_capabilities(self, mode: str) -> None:
    commands = self._MODE_CAPABILITY_COMMANDS.get(mode)
    if not commands:
      return
    try:
      await self.send_command(f"MODE {mode}")
    except TimeoutError:
      logger.warning("Capability MODE %s timed out; continuing without mode capabilities.", mode)
      return
    collected: Dict[str, str] = {}
    for cmd in commands:
      try:
        frames = await self.send_command(cmd)
      except TimeoutError:
        logger.warning("Capability query '%s' timed out; proceeding with defaults.", cmd)
        continue
      if frames:
        collected[cmd] = frames[-1]
    if collected:
      self._mode_capabilities[mode] = collected

  def get_mode_capability(self, mode: str, command: str) -> Optional[str]:
    return self._mode_capabilities.get(mode, {}).get(command)

  def capability_numeric(self, mode: str, command: str, fallback: int) -> int:
    resp = self.get_mode_capability(mode, command)
    if not resp:
      return fallback
    token = resp.split("|")[0].split(":")[0].split("~")[0].strip()
    if not token:
      return fallback
    try:
      return int(float(token))
    except ValueError:
      return fallback

  # -- mode settings --

  async def clear_mode_settings(self, excitation: bool = False, emission: bool = False) -> None:
    """Clear mode settings before configuring a new scan."""
    if excitation:
      await self.send_command("EXCITATION CLEAR", allow_timeout=True)
    if emission:
      await self.send_command("EMISSION CLEAR", allow_timeout=True)
    await self.send_command("TIME CLEAR", allow_timeout=True)
    await self.send_command("GAIN CLEAR", allow_timeout=True)
    await self.send_command("READS CLEAR", allow_timeout=True)
    await self.send_command("POSITION CLEAR", allow_timeout=True)
    await self.send_command("MIRROR CLEAR", allow_timeout=True)

  # -- geometry --

  def map_well_to_stage(self, well: Well) -> StagePosition:
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

  def group_by_row(self, wells: Sequence[Well]) -> List[Tuple[int, List[Well]]]:
    grouped: Dict[int, List[Well]] = {}
    for well in wells:
      grouped.setdefault(well.get_row(), []).append(well)
    for row in grouped.values():
      row.sort(key=lambda w: w.get_column())
    return sorted(grouped.items(), key=lambda item: item[0])

  def scan_visit_order(self, wells: Sequence[Well], serpentine: bool) -> List[Well]:
    visit: List[Well] = []
    for row_index, row_wells in self.group_by_row(wells):
      if serpentine and row_index % 2 == 1:
        visit.extend(reversed(row_wells))
      else:
        visit.extend(row_wells)
    return visit

  def scan_range(
    self, row_index: int, row_wells: Sequence[Well], serpentine: bool
  ) -> Tuple[int, int, int]:
    first_x, _ = self.map_well_to_stage(row_wells[0])
    last_x, _ = self.map_well_to_stage(row_wells[-1])
    count = len(row_wells)
    if not serpentine:
      return min(first_x, last_x), max(first_x, last_x), count
    if row_index % 2 == 0:
      return first_x, last_x, count
    return last_x, first_x, count

  # -- internal helpers --

  async def _initialize_device(self) -> None:
    try:
      await self.send_command("QQ")
    except TimeoutError:
      logger.warning("QQ produced no response; continuing with initialization.")
    await self.send_command("INIT FORCE")

  async def _cleanup_protocol(self) -> None:
    async def send_cleanup_cmd(cmd: str) -> None:
      try:
        await self.send_command(cmd, allow_timeout=True, read_response=False)
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

  async def _await_measurements(
    self, decoder: _MeasurementDecoder, row_count: int, mode: str
  ) -> None:
    target = decoder.count + row_count
    start_count = decoder.count
    self.drain_pending_bin_events(decoder)
    start = time.monotonic()
    reads = 0
    while decoder.count < target and (time.monotonic() - start) < self._max_row_wait_s:
      chunk = await self.read_packet(self._read_chunk_size)
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

  def drain_pending_bin_events(self, decoder: _MeasurementDecoder) -> None:
    if not self._pending_bin_events:
      return
    for payload_len, blob in self._pending_bin_events:
      decoder.feed_bin(payload_len, blob)
    self._pending_bin_events.clear()

  async def _await_scan_terminal(self, saw_terminal: bool) -> None:
    if saw_terminal:
      return
    await self._read_command_response()

  def _reset_stream_state(self) -> None:
    self._pending_bin_events.clear()
    self._parser = _StreamParser(allow_bare_ascii=True)

  async def _read_command_response(
    self, max_iterations: int = 8, require_terminal: bool = True
  ) -> List[str]:
    """Read response frames and cache any binary payloads that arrive."""
    frames: List[str] = []
    saw_terminal = False
    for _ in range(max_iterations):
      chunk = await self.read_packet(128)
      if not chunk:
        break
      for event in self._parser.feed(chunk):
        if event.text is not None:
          frames.append(event.text)
          if is_terminal_frame(event.text):
            saw_terminal = True
        elif event.payload_len is not None and event.blob is not None:
          self._pending_bin_events.append((event.payload_len, event.blob))
      if not require_terminal and frames and not self._parser.has_pending_bin():
        break
      if require_terminal and saw_terminal and not self._parser.has_pending_bin():
        break
    if require_terminal and not saw_terminal:
      await self._drain(1)
    return frames

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

  async def _drain(self, attempts: int = 4) -> None:
    """Read and discard a few packets to clear the stream."""
    for _ in range(attempts):
      data = await self.read_packet(128)
      if not data:
        break
