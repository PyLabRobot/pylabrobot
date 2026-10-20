"""Bulk-in transfers kept pending on the reader's FTDI chip, so no byte the reader sends is lost.

While no bulk-in transfer is pending, the reader's FT232H keeps about 16 bytes of what the reader
sends and drops the rest, flagging an overrun. libftdi has one transfer pending at a time and
returns on each status-only packet, so a pause in a reply leaves the chip without one. These reads
keep many libusb transfers pending, on the handle libftdi opened, from setup until stop; libftdi
keeps the writes and control requests.

Each transfer completes on one short packet, one per latency period, and the kernel fills the
pending ones in order without Python: enough are kept to cover a host stall of `_LONGEST_STALL`
in which Python cannot submit them again.
"""

import asyncio
import atexit
import ctypes
import logging
import math
import threading
import time
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class _Transfer(ctypes.Structure):
  """libusb 1.0's `struct libusb_transfer`, without its isochronous packet descriptors."""

  _fields_ = [
    ("dev_handle", ctypes.c_void_p),
    ("flags", ctypes.c_uint8),
    ("endpoint", ctypes.c_ubyte),
    ("type", ctypes.c_ubyte),
    ("timeout", ctypes.c_uint),
    ("status", ctypes.c_int),
    ("length", ctypes.c_int),
    ("actual_length", ctypes.c_int),
    ("callback", ctypes.c_void_p),
    ("user_data", ctypes.c_void_p),
    ("buffer", ctypes.c_void_p),
    ("num_iso_packets", ctypes.c_int),
  ]


class _Timeval(ctypes.Structure):
  """`struct timeval`, the event handler's timeout."""

  _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _FtdiContextStart(ctypes.Structure):
  """The first two members of libftdi 1.x's `struct ftdi_context`, as pylibftdi reads them."""

  _fields_ = [("libusb_context", ctypes.c_void_p), ("libusb_device_handle", ctypes.c_void_p)]


_TRANSFER_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.POINTER(_Transfer))
_BULK_IN = 0x81  # libftdi's read endpoint for interface A, the FT232H's only one
_BULK = 2  # LIBUSB_TRANSFER_TYPE_BULK
_COMPLETED, _CANCELLED = 0, 3  # enum libusb_transfer_status
# One high-speed bulk packet: 2 status bytes (modem, line), then data. At 125000 baud a packet
# never fills, so each transfer completes on its first packet.
_PACKET_SIZE = 512
_LINE_ERRORS = 0x1E  # line status bits: overrun, parity, framing, break
_EVENT_TIMEOUT = _Timeval(0, 100_000)  # the event thread looks at its state at least this often
_STOP_TIMEOUT = 2.0  # seconds stop waits for cancelled transfers to complete
LATENCY_TIMER = 2  # ms the chip waits before sending a short packet; set by the driver
# Seconds of host stall the queued reads must ride out. The longest measured stall was 92 ms on
# the reader (finding 15); the margin covers garbage collection and GIL pauses at negligible cost.
_LONGEST_STALL = 0.25
_TRANSFERS = math.ceil(_LONGEST_STALL * 1000 / LATENCY_TIMER)  # one per latency period
_LOW_MARGIN = 0.1  # a stall leaving fewer than this share of the transfers pending is warned


def _declare_libusb_functions(libusb: Any) -> None:
  """Give the libusb functions these reads call their C signatures; pylibftdi calls none of them."""
  libusb.libusb_alloc_transfer.argtypes = [ctypes.c_int]
  libusb.libusb_alloc_transfer.restype = ctypes.POINTER(_Transfer)
  for function in (libusb.libusb_submit_transfer, libusb.libusb_cancel_transfer):
    function.argtypes = [ctypes.POINTER(_Transfer)]
    function.restype = ctypes.c_int
  libusb.libusb_free_transfer.argtypes = [ctypes.POINTER(_Transfer)]
  libusb.libusb_free_transfer.restype = None
  libusb.libusb_handle_events_timeout_completed.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(_Timeval),
    ctypes.POINTER(ctypes.c_int),
  ]
  libusb.libusb_handle_events_timeout_completed.restype = ctypes.c_int


class QueuedReads:
  """Bulk-in transfers kept pending from `start` until `stop`; `take` hands over what they read.

  A completed transfer is submitted again from its callback, after its data is copied out. One
  thread handles libusb's events; libftdi's own calls may handle them too, and so run callbacks.
  A callback that copied out bytes, or left no transfer pending, wakes `wait` in the event loop
  that started the reads.
  """

  error: Optional[str]  # the first failure: a transfer's status, or a submit's result
  _loop: asyncio.AbstractEventLoop  # the loop `start` ran in, before any transfer is submitted
  _wake: asyncio.Event  # set when bytes arrive or no transfer is pending; cleared by `wait`
  _stopping: threading.Event  # set by `stop`
  _stop_deadline: float  # when the event thread gives up on cancelled transfers
  _completed: int  # transfers completed and submitted again since the last margin check

  def _set_wake(self) -> None:
    """Set `_wake` in the event loop that started the reads; nothing once that loop closed."""
    try:
      self._loop.call_soon_threadsafe(self._wake.set)
    except RuntimeError:  # the loop closed
      pass

  def _on_complete(self, pointer: Any) -> None:
    """Copy out a completed transfer's data, then submit it again unless stopping or failed."""
    received = False
    try:
      transfer = pointer.contents
      status, length = transfer.status, transfer.actual_length
      data = ctypes.string_at(transfer.buffer, length) if length > 0 else b""
      with self._lock:
        for start in range(0, len(data), _PACKET_SIZE):
          if start + 1 < len(data) and data[start + 1] & _LINE_ERRORS:
            self._line_errors += 1
          payload = data[start + 2 : start + _PACKET_SIZE]
          self._received += payload
          received = received or bool(payload)
        if status == _COMPLETED and not self._stopping.is_set():
          self._completed += 1
          result = self._libusb.libusb_submit_transfer(pointer)
          if result == 0:
            return
          self.error = self.error or f"libusb_submit_transfer returned {result}"
        elif status not in (_COMPLETED, _CANCELLED):
          self.error = self.error or f"transfer status {status}"
        self._pending -= 1
        received = received or self._pending == 0
    except Exception:  # an exception cannot leave a ctypes callback
      logger.exception("queued read callback failed")
      with self._lock:
        self._pending -= 1
      received = True
    finally:
      if received:
        self._set_wake()

  def _check_margin(self) -> None:
    """Warn once per stall that left fewer than `_LOW_MARGIN` of the transfers pending; re-armed
    once the transfers are pending again.

    The transfers completed since the last check, in one burst after a stall, were not pending.
    """
    with self._lock:
      completed = self._completed
      self._completed = 0
    lowest = self._pool - completed
    if lowest >= self._pool * _LOW_MARGIN:
      self._margin_warned = False
    elif not self._margin_warned:
      self._margin_warned = True
      logger.warning("a host stall left %d of %d bulk-in transfers pending", lowest, self._pool)

  def _handle_events(self) -> None:
    """Handle libusb's events until no transfer is pending, or `stop` has waited long enough."""
    while self._pending > 0:
      if self._stopping.is_set() and time.monotonic() > self._stop_deadline:
        return
      self._libusb.libusb_handle_events_timeout_completed(
        self._usb_context, ctypes.byref(_EVENT_TIMEOUT), None
      )
      self._check_margin()

  def __init__(
    self, libusb: Any, usb_context: int, handle: int, *, transfers: int = _TRANSFERS
  ) -> None:
    """
    Args:
      libusb: the libusb library pylibftdi loaded.
      usb_context: libftdi's libusb context.
      handle: libftdi's libusb device handle.
      transfers: how many transfers to keep pending.
    """
    _declare_libusb_functions(libusb)
    self._libusb, self._usb_context, self._handle = libusb, usb_context, handle
    self._transfer_count = transfers
    self._lock = threading.Lock()
    self._received = bytearray()
    self._line_errors = 0
    self._pending = 0
    self._pool = 0  # transfers submitted by `start`
    self._completed = 0
    self._margin_warned = False
    self._stopping = threading.Event()
    self._stop_deadline = 0.0
    self._wake = asyncio.Event()
    self.error = None
    self._callback = _TRANSFER_CALLBACK(self._on_complete)  # alive as long as transfers may run
    self._buffers: List[Any] = []
    self._transfers: List[Any] = []
    self._thread = threading.Thread(target=self._handle_events, name="queued reads", daemon=True)

  @classmethod
  def from_device(cls, device: Any) -> "QueuedReads":
    """Reads on the libusb handle a pylibftdi `Device` opened, through the libusb it loaded.

    Raises:
      OSError: if the device has no libusb handle.
    """
    start = ctypes.cast(ctypes.byref(device.ctx), ctypes.POINTER(_FtdiContextStart)).contents
    if not start.libusb_device_handle:
      raise OSError("the FTDI device has no libusb handle")
    return cls(device.driver._libusb, start.libusb_context, start.libusb_device_handle)

  @property
  def running(self) -> bool:
    """Whether transfers are pending and `stop` has not been called."""
    return self._pending > 0 and not self._stopping.is_set()

  def _free_transfers(self) -> None:
    """Free every allocated transfer; only when none is pending."""
    for pointer in self._transfers:
      self._libusb.libusb_free_transfer(pointer)
    self._transfers, self._buffers = [], []

  def take(self) -> Tuple[bytes, int]:
    """The bytes read since the last take, and how many of their packets flagged a line error."""
    with self._lock:
      data, errors = bytes(self._received), self._line_errors
      self._received.clear()
      self._line_errors = 0
    return data, errors

  async def wait(self, timeout: float) -> None:
    """Wait until bytes arrive or no transfer is pending, at most `timeout` seconds.

    Bytes that arrived since the last wait end it at once; `take` hands them over.
    """
    # Not wait_for: before Python 3.12 it drops a cancel that comes as the event is set.
    timer = asyncio.get_running_loop().call_later(max(timeout, 0), self._wake.set)
    try:
      await self._wake.wait()
    finally:
      timer.cancel()
    self._wake.clear()

  def stop(self) -> None:
    """Cancel the transfers, wait for them to complete, and free them.

    A transfer still pending after ``_STOP_TIMEOUT`` stays allocated (freeing it is undefined).
    """
    if self._stopping.is_set():
      return
    self._stop_deadline = time.monotonic() + _STOP_TIMEOUT
    self._stopping.set()
    atexit.unregister(self.stop)
    for pointer in self._transfers:
      self._libusb.libusb_cancel_transfer(pointer)  # a completed one answers NOT_FOUND
    if self._thread.is_alive():
      self._thread.join(_STOP_TIMEOUT + 1.0)
    pending = self._pending
    if pending == 0:
      self._free_transfers()
    else:
      logger.warning("%d bulk-in transfers still pending after stop; left allocated", pending)

  def start(self) -> None:
    """Allocate and submit the transfers, then start the event thread; `wait` runs in the
    running event loop.

    Raises:
      RuntimeError: if no event loop is running.
      OSError: if no transfer could be allocated and submitted.
    """
    self._loop = asyncio.get_running_loop()
    result = 0
    for _ in range(self._transfer_count):
      pointer = self._libusb.libusb_alloc_transfer(0)
      if not pointer:
        break
      buffer = ctypes.create_string_buffer(_PACKET_SIZE)
      transfer = pointer.contents
      transfer.dev_handle, transfer.endpoint, transfer.type = self._handle, _BULK_IN, _BULK
      transfer.timeout = 0  # never: libusb on macOS times a transfer out by aborting the pipe
      transfer.length, transfer.buffer = _PACKET_SIZE, ctypes.addressof(buffer)
      transfer.callback = ctypes.cast(self._callback, ctypes.c_void_p).value
      self._transfers.append(pointer)
      self._buffers.append(buffer)
      with self._lock:
        result = self._libusb.libusb_submit_transfer(pointer)
        if result != 0:
          break
        self._pending += 1
    self._pool = self._pending
    if self._pending == 0:
      self._free_transfers()
      raise OSError(f"no bulk-in transfer submitted (libusb {result})")
    if self._pending < self._transfer_count:
      logger.warning("%d of %d bulk-in transfers pending", self._pending, self._transfer_count)
    atexit.register(self.stop)  # before the interpreter closes the device under the transfers
    self._thread.start()
