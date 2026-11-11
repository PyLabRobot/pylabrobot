"""Replies read through bulk-in transfers kept pending, over a model of the reader's FTDI chip."""

import ast
import asyncio
import ctypes
import gzip
import json
import os
import threading
import time
import types
import unittest
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Deque, List, Optional, Tuple
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  RUN_REPLY,
  STATUS_MEASURED,
  STOP_FRAME,
  STOP_REPLY,
  ground_truth_96_well_plate,
  make_absorbance,
)
from pylabrobot.bmg_labtech.clariostar.driver.master import CLARIOstarDriver
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_BUSY,
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  FakeFTDI,
  make_driver,
)
from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.protocol.framing import ftdi_extract_payload
from pylabrobot.bmg_labtech.transport import ftdi_interface as ftdi_interface_module
from pylabrobot.bmg_labtech.transport import ftdi_queued_reads
from pylabrobot.bmg_labtech.transport.ftdi_queued_reads import QueuedReads
from pylabrobot.io.validation_utils import LOG_LEVEL_IO

DATA_REQUEST = bytes.fromhex("05020000000000")
DATA_REQUEST_FRAME = bytes.fromhex("02000f0c050200000000000000240d")

Packets = List[Tuple[float, bytes]]


async def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
  """Poll `condition` every millisecond; raise TimeoutError after `timeout` seconds."""

  async def poll() -> None:
    while not condition():
      await asyncio.sleep(0.001)

  await asyncio.wait_for(poll(), timeout)


def load_spectrum_page_transfers() -> Tuple[bytes, Packets, Tuple[int, float]]:
  """Two pages of a spectrum read: every transfer libusb completed, one pending at a time.

  Returns:
    Page 3 as received whole; its packets' payloads at their times (s after the data request);
    and page 4's stall: no transfer completed for 12.14 ms after transfer 1518, not even a
    status-only one, so none was pending. Page 4 lost 39 bytes there; both pages are at byte
    25,527 at that transfer.
  """
  path = os.path.join(
    os.path.dirname(__file__), "features", "test_data", "spectrum_page_transfers.json.gz"
  )
  with gzip.open(path, "rt") as f:
    run = json.load(f)
  page = bytes.fromhex(run["page_3"])
  packets, offset = [], 0
  for milliseconds, size in run["page_3_transfers"]:
    packets.append((milliseconds / 1000, page[offset : offset + size]))
    offset += size
  stall = run["page_4_stall"]
  return page, packets, (stall["after_transfer"], stall["ms"] / 1000)


class ChipModel:
  """Not a reader's reply: the FT232H as measured on a reader.

  Each `start` queues the next reply behind the one still streaming, which the reader sends to
  its end: its packets come at their times. While a transfer is
  pending each goes to the host after the status bytes `02 60`; while none is, the chip keeps
  16 bytes, drops the rest and flags the overrun (`02 62`) in the next packet. With no reply
  streaming it sends status-only packets every 2 ms.
  """

  _LATENCY = 0.002

  def __init__(self, *replies: Packets, stall: Optional[Tuple[int, float]] = None) -> None:
    """
    Args:
      replies: each reply's packets, (seconds after its start, payload).
      stall: (packet, seconds): the host neither submits nor completes transfers for that long
        after the first reply's packet.
    """
    self._replies: Deque[Packets] = deque(replies)
    self._packets: Deque[Tuple[int, float, bytes]] = deque()
    self._stall = stall
    self._stall_window = (0.0, -1.0)
    self._kept = bytearray()
    self._overrun = False
    self._lock = threading.Lock()
    self.time = 0.0
    self.dropped = 0

  def start(self) -> None:
    """Queue the next reply, as the reader answers a write: after the reply still streaming."""
    with self._lock:
      packets = self._replies.popleft()
      begin = self._packets[-1][1] if self._packets else self.time
      self._packets.extend((i, begin + t, payload) for i, (t, payload) in enumerate(packets))
      if self._stall is not None:
        after, seconds = self._stall
        stall_begin = begin + packets[after][0]
        self._stall_window, self._stall = (stall_begin, stall_begin + seconds), None

  def streaming(self) -> bool:
    with self._lock:
      return bool(self._packets)

  def remaining(self) -> int:
    """How many of the queued replies' packets are still to come."""
    with self._lock:
      return len(self._packets)

  def stalled(self) -> bool:
    """Whether the next packet comes while the host stalls."""
    with self._lock:
      begin, end = self._stall_window
      return bool(self._packets) and begin < self._packets[0][1] <= end

  def _next_payload(self) -> bytes:
    if not self._packets:
      self.time += self._LATENCY
      return b""
    _, at, payload = self._packets.popleft()
    self.time = max(self.time, at)
    return payload

  def send(self) -> bytes:
    """The next packet, to a pending transfer."""
    with self._lock:
      payload = self._next_payload()
      packet = bytes([0x02, 0x62 if self._overrun else 0x60]) + bytes(self._kept) + payload
      self._kept.clear()
      self._overrun = False
      return packet

  def drop(self) -> None:
    """The next packet, while no transfer is pending."""
    with self._lock:
      payload = self._next_payload()
      room = 16 - len(self._kept)
      self._kept += payload[:room]
      if len(payload) > room:
        self.dropped += len(payload) - room
        self._overrun = True


class LibftdiFTDI(FakeFTDI):
  """`FTDI.read` as libftdi reads the chip model: one transfer pending at a time, until
  `num_bytes` are in or a status-only packet ends the read; excess bytes kept for the next. As
  `FTDI`, one executor thread reads, so a cancelled read finishes in it."""

  def __init__(self, chip: ChipModel) -> None:
    super().__init__()
    self._device_id = "test"
    self.chip = chip
    self._buffered = bytearray()
    self._executor = ThreadPoolExecutor(max_workers=1)

  async def write(self, data: bytes) -> int:
    self.written.append(bytes(data))
    self.chip.start()
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(self._executor, self._read_now, num_bytes)

  def _read_now(self, num_bytes: int) -> bytes:
    data = self._buffered
    while len(data) < num_bytes:
      while self.chip.stalled():  # the host submits nothing
        self.chip.drop()
      payload = self.chip.send()[2:]
      if not payload:
        break
      data += payload
    self._buffered = data[num_bytes:]
    return bytes(data[:num_bytes])


class PacedChipModel(ChipModel):
  """Not a reader's reply: `ChipModel` sending each packet no earlier than `pace` times its time
  after the reply's start, in real seconds, so the reader still streams while the host acts;
  status-only packets in between."""

  def __init__(self, *replies: Packets, pace: float) -> None:
    """
    Args:
      replies: as `ChipModel`'s.
      pace: real seconds per second of the replies' times.
    """
    super().__init__(*replies)
    self._pace = pace
    self._anchor = (0.0, 0.0)

  def start(self) -> None:
    idle = not self.streaming()
    super().start()
    if idle:
      self._anchor = (time.monotonic(), self.time)

  def _next_payload(self) -> bytes:
    """The next packet's payload once it is due; until then, one latency period later, none: the
    chip's status-only packet."""
    if self._packets:
      real, chip = self._anchor
      wait = real + (self._packets[0][1] - chip) * self._pace - time.monotonic()
      if wait > 0:
        time.sleep(min(wait, self._LATENCY))
        return b""
    return super()._next_payload()


class FakeLibusb:
  """libusb's asynchronous transfers over the chip model, as the kernel runs them: pending
  transfers are filled in submission order, also while the host stalls and runs no callback."""

  def _handle_events(self) -> None:
    """Run the completed transfers' callbacks unless the host stalls, then fill the first
    pending transfer with the next packet."""
    with self._lock:
      ready = [] if self.chip.stalled() else list(self.completed)
      for pointer in ready:
        self.completed.remove(pointer)
    for pointer in ready:
      ftdi_queued_reads._TRANSFER_CALLBACK(pointer.contents.callback)(pointer)
    with self._lock:
      if self.submitted:
        pointer = self.submitted.popleft()
        packet = self.chip.send() if self.status == 0 else b""
        transfer = pointer.contents
        ctypes.memmove(transfer.buffer, packet, len(packet))
        transfer.actual_length, transfer.status = len(packet), self.status
        self.completed.append(pointer)
      else:
        self.chip.drop()
    if not self.chip.streaming():
      time.sleep(0.0002)

  def __init__(self, chip: ChipModel, status: int = 0) -> None:
    """
    Args:
      chip: the chip the transfers read.
      status: every transfer's status; 0 completed, 5 no device.
    """
    self.chip, self.status = chip, status
    self.submitted: Deque[Any] = deque()
    self.completed: Deque[Any] = deque()
    self.allocated: List[int] = []
    self.freed: List[int] = []
    self._lock = threading.Lock()

    def alloc(isochronous_packets: int) -> Any:
      pointer = ctypes.pointer(ftdi_queued_reads._Transfer())
      self.allocated.append(ctypes.addressof(pointer.contents))
      return pointer

    def submit(pointer: Any) -> int:
      with self._lock:
        self.submitted.append(pointer)
      return 0

    def cancel(pointer: Any) -> int:
      address = ctypes.addressof(pointer.contents)
      with self._lock:
        for pending in self.submitted:
          if ctypes.addressof(pending.contents) == address:
            self.submitted.remove(pending)
            pending.contents.status, pending.contents.actual_length = 3, 0
            self.completed.append(pending)
            return 0
      return -5  # LIBUSB_ERROR_NOT_FOUND

    def free(pointer: Any) -> None:
      self.freed.append(ctypes.addressof(pointer.contents))

    def handle_events(context: int, timeout: Any, completed: Any) -> int:
      self._handle_events()
      return 0

    self.functions = types.SimpleNamespace(
      libusb_alloc_transfer=alloc,
      libusb_submit_transfer=submit,
      libusb_cancel_transfer=cancel,
      libusb_free_transfer=free,
      libusb_handle_events_timeout_completed=handle_events,
    )


def keep_reads_queued(
  driver: CLARIOstarDriver, libusb: FakeLibusb, transfers: int = ftdi_queued_reads._TRANSFERS
) -> QueuedReads:
  """Start `transfers` pending transfers on `libusb` for `driver` to read through."""
  driver._ftdi._packet_read_timeout, driver._ftdi._frame_stall_timeout = 1.0, 0.3
  reads = QueuedReads(libusb.functions, 1, 2, transfers=transfers)
  reads.start()
  driver._ftdi._queued_reads = reads
  return reads


def make_queued_driver(
  io: FakeFTDI, libusb: FakeLibusb, transfers: int = ftdi_queued_reads._TRANSFERS
) -> CLARIOstarDriver:
  """A driver writing to `io` and reading through `transfers` pending transfers on `libusb`."""
  driver = make_driver(io)
  keep_reads_queued(driver, libusb, transfers)
  return driver


class QueuedReadsTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_stall_loses_bytes_with_libftdi_but_not_with_transfers_pending(self):
    page, packets, stall = load_spectrum_page_transfers()

    chip = ChipModel(packets, stall=stall)
    driver = make_driver(LibftdiFTDI(chip))
    driver._ftdi._packet_read_timeout = 1.0  # the driver's own: the page starts after 34 ms
    with patch.object(ftdi_interface_module.time, "time", side_effect=lambda: chip.time):
      with self.assertRaises(FrameError):
        await driver.exchange(DATA_REQUEST)
    self.assertGreater(chip.dropped, 0)

    chip = ChipModel(packets, stall=stall)
    io, libusb = LibftdiFTDI(chip), FakeLibusb(chip)
    driver = make_queued_driver(io, libusb)
    # No sleep and no timeout ends a wait: the event thread's callbacks wake the read.
    driver._ftdi._packet_read_timeout = driver._ftdi._frame_stall_timeout = 60.0
    with patch.object(ftdi_interface_module.asyncio, "sleep", side_effect=AssertionError("polled")):
      with self.assertNoLogs(ftdi_queued_reads.logger, "WARNING"):  # the stall used 6 of 125
        reply = await asyncio.wait_for(driver.exchange(DATA_REQUEST), 10)
    self.assertEqual(reply, ftdi_extract_payload(page))
    self.assertEqual(chip.dropped, 0)
    self.assertEqual(io.written, [DATA_REQUEST_FRAME])
    driver._ftdi._stop_queued_reads()
    self.assertEqual(sorted(libusb.freed), sorted(libusb.allocated))
    self.assertEqual(len(libusb.allocated), 125)  # 0.25 s of 2 ms latency periods

  async def test_a_stall_longer_than_the_transfers_cover_is_warned(self):
    # 4 transfers cover 8 of the stall's 12.14 ms.
    _, packets, stall = load_spectrum_page_transfers()
    chip = ChipModel(packets, stall=stall)
    driver = make_queued_driver(LibftdiFTDI(chip), FakeLibusb(chip), transfers=4)
    self.addCleanup(driver._ftdi._stop_queued_reads)
    with self.assertLogs("pylabrobot", "WARNING") as logs:
      with self.assertRaises(FrameError):
        await driver.exchange(DATA_REQUEST)
    self.assertGreater(chip.dropped, 0)
    self.assertTrue(any("flagged a line error" in line for line in logs.output))
    stalls = [line for line in logs.output if "host stall" in line]
    self.assertEqual(len(stalls), 1)  # once for the stall, not per transfer
    self.assertIn("left 0 of 4 bulk-in transfers pending", stalls[0])

  async def test_bytes_read_before_a_command_are_not_its_reply(self):
    # Not a reader's reply: the reader sends nothing unasked; a status reply read before the
    # write stands in for one left by a cancelled exchange.
    chip = ChipModel([(0.0, STATUS_BUSY)], [(0.004, STATUS_IDLE)])
    io = LibftdiFTDI(chip)
    driver = make_queued_driver(io, FakeLibusb(chip))
    self.addCleanup(driver._ftdi._stop_queued_reads)
    chip.start()
    await wait_until(lambda: not chip.streaming())
    sent = chip.time
    await wait_until(lambda: chip.time > sent + ChipModel._LATENCY)  # the callback has run
    status = await driver.request_status()
    self.assertFalse(status.busy)
    self.assertEqual(io.written, [STATUS_QUERY_FRAME])

  async def test_transfers_that_failed_leave_reading_to_libftdi(self):
    # Not a reader's reply: status 5 (no device) on every transfer, as libusb reports a removal.
    io, libusb = FakeFTDI(), FakeLibusb(ChipModel(), status=5)
    io.replies = [STATUS_IDLE]
    driver = make_queued_driver(io, libusb)
    reads = driver._ftdi._queued_reads
    assert reads is not None
    deadline = time.monotonic() + 5.0
    while reads.running:
      self.assertLess(time.monotonic(), deadline)
      await reads.wait(1.0)  # the last transfer to fail wakes it
    with self.assertLogs(ftdi_interface_module.logger, "WARNING"):
      status = await driver.request_status()
    self.assertFalse(status.busy)
    self.assertIsNone(driver._ftdi._queued_reads)
    self.assertEqual(sorted(libusb.freed), sorted(libusb.allocated))

  async def test_a_cancelled_spectrum_leaves_the_reads_running_and_the_link_clean(self):
    # Page 3 at a quarter of its packet times answers the data request; the read is cancelled
    # early in it, the reader sends it to its end, and the drain takes the rest before the STOP.
    page, packets, _ = load_spectrum_page_transfers()
    before = ([(0.0, reply)] for reply in (STATUS_IDLE, RUN_REPLY, STATUS_MEASURED))
    chip = PacedChipModel(*before, packets, [(0.0, STOP_REPLY)], [(0.0, STATUS_IDLE)], pace=0.25)
    io, libusb = LibftdiFTDI(chip), FakeLibusb(chip)
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    driver = absorbance._driver
    reads = keep_reads_queued(driver, libusb)
    self.addCleanup(driver._ftdi._stop_queued_reads)
    with self.assertLogs("pylabrobot", LOG_LEVEL_IO) as logs:
      task = asyncio.create_task(
        absorbance.measure_spectrum(start_wavelength=220, end_wavelength=1000, step_size=1)
      )
      await wait_until(
        lambda: DATA_REQUEST_FRAME in io.written and chip.remaining() <= 0.9 * len(packets)
      )
      self.assertTrue(chip.streaming())
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
      status = await driver.request_status()
    self.assertFalse(status.busy)
    self.assertEqual(io.written[3:], [DATA_REQUEST_FRAME, STOP_FRAME, STATUS_QUERY_FRAME])
    messages = [record.getMessage() for record in logs.records]
    first_discard = next(i for i, m in enumerate(messages) if " discarded " in m)
    read = next(m for m in reversed(messages[:first_discard]) if " read " in m)
    taken = ast.literal_eval(read.split(" read ")[1])  # logged although the read was cancelled
    late = ast.literal_eval(messages[first_discard].split(" discarded ")[1])
    self.assertEqual(taken + late, page)  # the drained rest, before the STOP's write
    self.assertGreater(len(late), len(page) // 2)
    self.assertIs(driver._ftdi._get_queued_reads(), reads)
    self.assertTrue(reads.running)
    self.assertEqual(reads.take(), (b"", 0))
    self.assertEqual(chip.dropped, 0)
    self.assertFalse(
      any("line error" in m or "not accepted" in m or "failed (attempt" in m for m in messages)
    )

  async def test_a_cancelled_spectrum_through_libftdi_leaves_the_link_clean(self):
    # As above with no reads queued: libftdi's reads drain the page's rest before the STOP.
    _, packets, _ = load_spectrum_page_transfers()
    before = ([(0.0, reply)] for reply in (STATUS_IDLE, RUN_REPLY, STATUS_MEASURED))
    chip = PacedChipModel(*before, packets, [(0.0, STOP_REPLY)], [(0.0, STATUS_IDLE)], pace=0.25)
    io = LibftdiFTDI(chip)
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    driver = absorbance._driver
    driver._ftdi._packet_read_timeout, driver._ftdi._frame_stall_timeout = 1.0, 0.3
    self.assertIsNone(driver._ftdi._queued_reads)
    with self.assertLogs("pylabrobot", LOG_LEVEL_IO) as logs:
      task = asyncio.create_task(
        absorbance.measure_spectrum(start_wavelength=220, end_wavelength=1000, step_size=1)
      )
      await wait_until(
        lambda: DATA_REQUEST_FRAME in io.written and chip.remaining() <= 0.9 * len(packets)
      )
      self.assertTrue(chip.streaming())
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
      status = await driver.request_status()
    self.assertFalse(status.busy)
    self.assertEqual(io.written[3:], [DATA_REQUEST_FRAME, STOP_FRAME, STATUS_QUERY_FRAME])
    self.assertFalse(chip.streaming())
    messages = [record.getMessage() for record in logs.records]
    self.assertFalse(any("not accepted" in m or "failed (attempt" in m for m in messages))


if __name__ == "__main__":
  unittest.main()
