"""A BMG reader's FTDI interface: it writes a frame and reads exactly one reply frame back.

The reply ends where its size field says (CR also occurs inside payloads). Replies are read
through bulk-in transfers kept pending from `setup` to `stop` (`ftdi_queued_reads`), or with
libftdi when those cannot run.
"""

import asyncio
import logging
import time
from typing import Optional

from pylabrobot.bmg_labtech.protocol.framing import (
  FTDI_CR,
  FTDI_STX,
  ftdi_extract_payload,
  ftdi_validate_frame,
  ftdi_wrap_payload,
)
from pylabrobot.io.capture import get_capture_or_validation_active
from pylabrobot.io.ftdi import FTDI
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

from .ftdi_queued_reads import LATENCY_TIMER, QueuedReads

logger = logging.getLogger(__name__)


class BMGFTDIInterface:
  """Exchanges BMG frames with one reader over FTDI, one reply frame per frame written."""

  _FTDI_READ_CHUNK = 25  # bytes per read; a shorter read marks the end of a burst

  def __init__(
    self,
    io: FTDI,
    *,
    header: int,
    baudrate: int,
    packet_read_timeout: float,
    frame_stall_timeout: float,
    frame_hard_cap: float,
  ):
    """
    Args:
      io: the reader's FTDI connection.
      header: the reader's frame header byte.
      baudrate: the reader's baud rate.
      packet_read_timeout: seconds to wait for the start of a reply.
      frame_stall_timeout: seconds without new bytes before a started frame is abandoned.
      frame_hard_cap: seconds; ceiling on reading one frame.
    """
    self.io = io
    self.header = header
    self.baudrate = baudrate
    self._packet_read_timeout = packet_read_timeout
    self._frame_stall_timeout = frame_stall_timeout
    self._frame_hard_cap = frame_hard_cap
    # Bulk-in transfers kept pending from setup to stop; None reads through libftdi.
    self._queued_reads: Optional[QueuedReads] = None

  def _start_queued_reads(self) -> None:
    """Keep bulk-in transfers pending on the open FTDI chip; if they cannot start, read with
    libftdi (`_read_frame`).

    Not while capture or validation is active: their replay needs every read to pass `FTDI.read`.
    The queued reads belong to the event loop that ran setup.
    """
    if get_capture_or_validation_active():
      logger.info("capture or validation active: reading with libftdi")
      return
    try:
      reads = QueuedReads.from_device(self.io.dev)
      reads.start()
    except Exception as e:  # no open pylibftdi device, no libusb handle, nothing submitted
      logger.warning("bulk-in transfers not kept pending (%s); reading with libftdi", e)
      return
    self._queued_reads = reads

  def _stop_queued_reads(self) -> None:
    """Cancel and free the pending bulk-in transfers, if any; libftdi reads from then on."""
    reads, self._queued_reads = self._queued_reads, None
    if reads is not None:
      reads.stop()

  async def setup(self) -> None:
    """Open the FTDI port at the reader's baud rate, 8N1, latency 2 ms, and keep bulk-in
    transfers pending."""
    self._stop_queued_reads()  # a second setup reopens the device under them
    await self.io.setup()
    await self.io.set_baudrate(self.baudrate)
    await self.io.set_line_property(8, 0, 0)  # 8N1
    await self.io.set_latency_timer(LATENCY_TIMER)
    self._start_queued_reads()

  async def _write(self, frame: bytes) -> None:
    """Write a whole frame; raise OSError on a short write."""
    written = await self.io.write(frame)
    if written != len(frame):
      raise OSError(f"Short write: sent {written} of {len(frame)} bytes")

  async def _read_frame(self) -> bytes:
    """Read one FTDI reply frame.

    CR (0x0D) also occurs inside payloads, so once the header is in, the size field decides
    when the frame is complete. Bytes before an STX are dropped to resync. Before the header,
    ``packet_read_timeout`` bounds the wait; after it, only a silence of
    ``frame_stall_timeout`` or the ``frame_hard_cap`` ends the read, as long replies
    stream in bursts.

    Returns:
      The frame, possibly short of its size field on a stall; the caller validates it.
    """
    data = b""
    expected_size: Optional[int] = None
    end_byte_found = False
    start = time.time()
    last_data_time = start

    while True:
      chunk = await self.io.read(self._FTDI_READ_CHUNK)
      if chunk:
        data += chunk
        last_data_time = time.time()

        if expected_size is None and data[0] != FTDI_STX:
          index = data.find(FTDI_STX)
          data = data[index:] if index != -1 else b""

        end_byte_found = bool(data) and data[-1] == FTDI_CR

        if expected_size is None and len(data) >= 3:
          expected_size = int.from_bytes(data[1:3], "big")
          start = time.time()

        if len(chunk) < self._FTDI_READ_CHUNK and end_byte_found:
          if expected_size is None or len(data) >= expected_size:
            break
        if expected_size is not None and len(data) >= expected_size:
          break

        # The size is known, so ask for exactly the rest of the frame.
        if expected_size is not None:
          remainder = await self.io.read(expected_size - len(data))
          if remainder:
            data += remainder
            last_data_time = time.time()
            end_byte_found = data[-1] == FTDI_CR
            if len(data) >= expected_size:
              break
      else:
        if end_byte_found and (expected_size is None or len(data) >= expected_size):
          break
        now = time.time()
        if expected_size is None:
          timed_out = now - start > self._packet_read_timeout
        else:
          timed_out = (
            now - last_data_time > self._frame_stall_timeout or now - start > self._frame_hard_cap
          )
        if timed_out:
          if expected_size is not None and 0 < len(data) < expected_size:
            data += await self.io.read(expected_size - len(data))
          break
        await asyncio.sleep(0.0001)

    if expected_size is not None and len(data) >= expected_size:
      return data[:expected_size]
    return data

  def _get_queued_reads(self) -> Optional[QueuedReads]:
    """The queued reads while they run; None once they stopped by themselves (e.g. the device
    was removed), and libftdi reads from then on."""
    reads = self._queued_reads
    if reads is not None and not reads.running:
      logger.warning("bulk-in transfers stopped (%s); reading with libftdi", reads.error)
      self._stop_queued_reads()
      return None
    return reads

  def _discard_unread(self, reads: QueuedReads) -> None:
    """Drop the bytes the queued reads hold unread, logging them at the I/O level."""
    data, _ = reads.take()
    if data:
      logger.log(LOG_LEVEL_IO, "[%s] discarded %s", self.io.device_id, data)

  async def _read_queued_frame(self, reads: QueuedReads) -> bytes:
    """Read one FTDI reply frame from the queued reads, with `_read_frame`'s rules.

    Bytes before an STX are dropped and the size field ends the frame. It waits until the pending
    reads receive bytes: ``packet_read_timeout`` bounds the wait for the header; after it, a
    silence of ``frame_stall_timeout`` or the ``frame_hard_cap``. Packets flagging a line error
    (an overrun loses bytes) are warned about. The IO log gets one line per reply: every byte
    taken, dropped ones included.

    Returns:
      The frame, possibly short of its size field on a stall; the caller validates it.
    """
    data = bytearray()
    received = bytearray()
    expected_size: Optional[int] = None
    line_errors = 0
    start = time.time()
    last_data_time = start
    try:
      while True:
        chunk, errors = reads.take()
        line_errors += errors
        now = time.time()
        if chunk:
          received += chunk
          data += chunk
          last_data_time = now
          if expected_size is None:
            index = data.find(FTDI_STX)
            del data[: index if index != -1 else len(data)]
            if len(data) >= 3:
              expected_size = int.from_bytes(data[1:3], "big")
              start = now
          if expected_size is not None and len(data) >= expected_size:
            break
        elif not reads.running:
          break
        if expected_size is None:
          deadline = start + self._packet_read_timeout
        else:
          deadline = min(last_data_time + self._frame_stall_timeout, start + self._frame_hard_cap)
        if now > deadline:
          break
        await reads.wait(deadline - now)
    finally:  # a cancelled read's bytes too
      if received:
        logger.log(LOG_LEVEL_IO, "[%s] read %s", self.io.device_id, bytes(received))
    if line_errors:
      logger.warning("%d packets of this reply flagged a line error (bytes lost)", line_errors)
    if expected_size is not None and len(data) >= expected_size:
      return bytes(data[:expected_size])
    return bytes(data)

  async def exchange(self, payload: bytes) -> bytes:
    """Send one payload over FTDI and return the validated reply payload."""
    reads = self._get_queued_reads()
    if reads is None:
      await self._write(ftdi_wrap_payload(payload, header=self.header))
      frame = await self._read_frame()
    else:
      self._discard_unread(reads)  # bytes read before the command is sent are not its reply
      await self._write(ftdi_wrap_payload(payload, header=self.header))
      frame = await self._read_queued_frame(reads)
    ftdi_validate_frame(frame, header=self.header)
    return ftdi_extract_payload(frame)

  async def purge_rx(self) -> None:
    """Discard unread reply bytes."""
    await self.io.usb_purge_rx_buffer()
    if self._queued_reads is not None:
      self._discard_unread(self._queued_reads)

  async def drain_rx(self) -> None:
    """Discard reply bytes until none has come for ``frame_stall_timeout``, at most
    ``frame_hard_cap``: the rest of a reply the reader is still sending.

    The reader sends a reply it has started to its end, a data page too (61,508 B over about
    7 s); after a cancelled read its rest would reach the next command's read. The queued reads'
    bytes are logged on one I/O line, libftdi's by `FTDI.read`.
    """
    reads = self._get_queued_reads()
    drained = bytearray()
    start = last_data_time = time.monotonic()
    while True:
      if reads is not None:
        data, _ = reads.take()
      else:
        data = await self.io.read(self._FTDI_READ_CHUNK)
      now = time.monotonic()
      if data:
        drained += data
        last_data_time = now
      deadline = min(last_data_time + self._frame_stall_timeout, start + self._frame_hard_cap)
      if now >= deadline:
        break
      if reads is not None:
        await reads.wait(deadline - now)
      elif not data:
        await asyncio.sleep(LATENCY_TIMER / 1000)  # the chip sends at least once per period
    if reads is not None and drained:
      logger.log(LOG_LEVEL_IO, "[%s] discarded %s", self.io.device_id, bytes(drained))

  def stop(self) -> None:
    """Cancel the pending bulk-in transfers, before the FTDI device under them is closed."""
    self._stop_queued_reads()
