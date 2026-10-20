"""Bulk-in transfers kept pending on the reader's FTDI chip, so no byte the reader sends is lost.

While no bulk-in transfer is pending, the reader's FT232H keeps about 16 bytes of what the reader
sends and drops the rest, flagging an overrun. libftdi has one transfer pending at a time and
returns on each status-only packet, so a pause in a reply leaves the chip without one. These reads
keep many libusb transfers pending, on the handle libftdi opened, from setup until stop; libftdi
keeps the writes and control requests.

Each transfer completes on one short packet, one per latency period (2 ms), and the kernel fills
the pending ones in order without Python: 128 of them cover about 250 ms in which Python cannot
submit them again.
"""

import atexit
import ctypes
import logging
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


class PendingReads:
  """Bulk-in transfers kept pending from `start` until `stop`; `take` hands over what they read.

  A completed transfer is submitted again from its callback, after its data is copied out. One
  thread handles libusb's events; libftdi's own calls may handle them too, and so run callbacks.
  """

  error: Optional[str]  # the first failure: a transfer's status, or a submit's result

  def _on_complete(self, pointer: Any) -> None:
    """Copy out a completed transfer's data, then submit it again unless stopping or failed."""
    try:
      transfer = pointer.contents
      status, length = transfer.status, transfer.actual_length
      data = ctypes.string_at(transfer.buffer, length) if length > 0 else b""
      with self._lock:
        for start in range(0, len(data), _PACKET_SIZE):
          if start + 1 < len(data) and data[start + 1] & _LINE_ERRORS:
            self._line_errors += 1
          self._received += data[start + 2 : start + _PACKET_SIZE]
        if status == _COMPLETED and not self._stopping:
          result = self._libusb.libusb_submit_transfer(pointer)
          if result == 0:
            return
          self.error = self.error or f"libusb_submit_transfer returned {result}"
        elif status not in (_COMPLETED, _CANCELLED):
          self.error = self.error or f"transfer status {status}"
        self._pending -= 1
    except Exception:  # an exception cannot leave a ctypes callback
      logger.exception("pending read callback failed")
      with self._lock:
        self._pending -= 1

  def _handle_events(self) -> None:
    """Handle libusb's events until no transfer is pending, or `stop` has waited long enough."""
    while True:
      with self._lock:
        if self._pending == 0 or (self._stopping and time.monotonic() > self._stop_deadline):
          return
      self._libusb.libusb_handle_events_timeout_completed(
        self._usb_context, ctypes.byref(_EVENT_TIMEOUT), None
      )

  def __init__(self, libusb: Any, usb_context: int, handle: int, *, transfers: int = 128) -> None:
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
    self._stopping = False
    self._stop_deadline = 0.0
    self.error = None
    self._callback = _TRANSFER_CALLBACK(self._on_complete)  # alive as long as transfers may run
    self._buffers: List[Any] = []
    self._transfers: List[Any] = []
    self._thread = threading.Thread(target=self._handle_events, name="pending reads", daemon=True)

  @classmethod
  def from_device(cls, device: Any) -> "PendingReads":
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
    with self._lock:
      return self._pending > 0 and not self._stopping

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

  def stop(self) -> None:
    """Cancel the transfers, wait for them to complete, and free them.

    A transfer still pending after ``_STOP_TIMEOUT`` stays allocated (freeing it is undefined).
    """
    with self._lock:
      if self._stopping:
        return
      self._stopping = True
      self._stop_deadline = time.monotonic() + _STOP_TIMEOUT
    atexit.unregister(self.stop)
    for pointer in self._transfers:
      self._libusb.libusb_cancel_transfer(pointer)  # a completed one answers NOT_FOUND
    if self._thread.is_alive():
      self._thread.join(_STOP_TIMEOUT + 1.0)
    with self._lock:
      pending = self._pending
    if pending == 0:
      self._free_transfers()
    else:
      logger.warning("%d bulk-in transfers still pending after stop; left allocated", pending)

  def start(self) -> None:
    """Allocate and submit the transfers, then start the event thread.

    Raises:
      OSError: if no transfer could be allocated and submitted.
    """
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
    if self._pending == 0:
      self._free_transfers()
      raise OSError(f"no bulk-in transfer submitted (libusb {result})")
    if self._pending < self._transfer_count:
      logger.warning("%d of %d bulk-in transfers pending", self._pending, self._transfer_count)
    atexit.register(self.stop)  # before the interpreter closes the device under the transfers
    self._thread.start()
