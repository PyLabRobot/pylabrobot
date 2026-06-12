"""FTDI byte transport for the Celigo USB-IO controller board.

Open parameters:

* VID ``0x0403`` (1027), PID ``0x6001`` (24577) — standard FTDI.
* opened **by USB location id** (``FT_OpenByLocation``); serial number is not used.
* latency timer set to ``2`` ms.
* read timeout ``30000`` ms, write timeout ``5000`` ms.
* RX+TX purge immediately after open.

On Linux we reach the same chip through :mod:`pyftdi` (libusb), so no Windows/D2XX
dependency is needed. :class:`FtdiTransport` implements the small ``Transport`` protocol
(:func:`pylabrobot.celigo.packets.transact` needs ``write`` / ``read`` / ``purge``).

The board operates at 230400 baud (see :data:`DEFAULT_BAUDRATE`).
"""

from __future__ import annotations

import time
from typing import Any, Optional

# FTDI device constants.
FTDI_VID = 0x0403
FTDI_PID = 0x6001
LATENCY_TIMER_MS = 2
READ_TIMEOUT_MS = 30000
WRITE_TIMEOUT_MS = 5000
# The board operates at 230400 baud.
DEFAULT_BAUDRATE = 230_400


class FtdiTransport:
  """A ``Transport`` backed by a pyftdi-opened FTDI device.

  ``url`` selects the device using pyftdi's URL
  scheme (e.g. ``ftdi://ftdi:232:<serial>/1``); when omitted the first FTDI device
  matching :data:`FTDI_VID`/:data:`FTDI_PID` is used.
  """

  def __init__(
    self,
    url: Optional[str] = None,
    *,
    baudrate: int = DEFAULT_BAUDRATE,
    latency_ms: int = LATENCY_TIMER_MS,
    read_timeout_ms: int = READ_TIMEOUT_MS,
    write_timeout_ms: int = WRITE_TIMEOUT_MS,
  ):
    self.url = url
    self.baudrate = baudrate
    self.latency_ms = latency_ms
    self.read_timeout_ms = read_timeout_ms
    self.write_timeout_ms = write_timeout_ms
    self._ftdi: Any = None  # lazily created pyftdi.ftdi.Ftdi

  @property
  def is_open(self) -> bool:
    return self._ftdi is not None and self._ftdi.is_connected

  def open(self) -> None:
    """Open the device and apply the latency/timeout/purge setup."""
    try:
      from pyftdi.ftdi import Ftdi  # type: ignore
    except ImportError as exc:
      raise ImportError(
        "FtdiTransport requires pyftdi. Install it with `pip install pyftdi`."
      ) from exc

    ftdi = Ftdi()
    if self.url is not None:
      ftdi.open_from_url(self.url)
    else:
      ftdi.open(vendor=FTDI_VID, product=FTDI_PID)

    ftdi.set_latency_timer(self.latency_ms)
    # pyftdi expresses read/write timeouts in seconds on the usb context.
    ftdi.set_baudrate(self.baudrate)
    ftdi.purge_buffers()
    self._ftdi = ftdi

  def close(self) -> None:
    if self._ftdi is not None:
      try:
        self._ftdi.close()
      finally:
        self._ftdi = None

  def write(self, data: bytes) -> int:
    self._require_open()
    self._ftdi.write_data(data)
    return len(data)

  def read(self, n: int) -> bytes:
    """Read exactly ``n`` bytes, blocking up to the read timeout.

    pyftdi's ``read_data`` may return fewer bytes than requested, so accumulate until
    ``n`` are available or the timeout elapses (returning the short buffer, which the
    packet layer treats as an error).
    """
    self._require_open()
    deadline = time.monotonic() + self.read_timeout_ms / 1000.0
    chunks: "list[bytes]" = []
    remaining = n
    while remaining > 0:
      chunk = self._ftdi.read_data(remaining)
      if chunk:
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
        continue
      if time.monotonic() >= deadline:
        break
      time.sleep(0.001)
    return b"".join(chunks)

  def purge(self) -> None:
    self._require_open()
    self._ftdi.purge_buffers()

  def _require_open(self) -> None:
    if not self.is_open:
      raise RuntimeError("FtdiTransport is not open; call open() first.")

  def __enter__(self) -> "FtdiTransport":
    self.open()
    return self

  def __exit__(self, *exc) -> None:
    self.close()


class SerialTransport:
  """A ``Transport`` over a serial port (the FTDI board bound to ``ftdi_sio``).

  When the kernel's ``ftdi_sio`` driver claims the board it appears as ``/dev/ttyUSB*``;
  this talks to it with :mod:`pyserial` (no libusb / driver detach needed). Pass the
  appropriate baudrate value (230400 for the board).
  """

  def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0):
    self.port = port
    self.baudrate = baudrate
    self.timeout = timeout
    self._serial: Any = None

  @property
  def is_open(self) -> bool:
    return self._serial is not None and self._serial.is_open

  def open(self) -> None:
    try:
      import serial  # type: ignore
    except ImportError as exc:
      raise ImportError("SerialTransport requires pyserial (`pip install pyserial`).") from exc
    self._serial = serial.Serial(
      self.port, baudrate=self.baudrate, timeout=self.timeout, write_timeout=self.timeout
    )
    self.purge()

  def close(self) -> None:
    if self._serial is not None:
      try:
        self._serial.close()
      finally:
        self._serial = None

  def write(self, data: bytes) -> int:
    self._require_open()
    return self._serial.write(data) or 0

  def read(self, n: int) -> bytes:
    self._require_open()
    return bytes(self._serial.read(n))  # blocks up to `timeout`, may return fewer bytes

  def purge(self) -> None:
    self._require_open()
    self._serial.reset_input_buffer()
    self._serial.reset_output_buffer()

  def _require_open(self) -> None:
    if not self.is_open:
      raise RuntimeError("SerialTransport is not open; call open() first.")

  def __enter__(self) -> "SerialTransport":
    self.open()
    return self

  def __exit__(self, *exc) -> None:
    self.close()
