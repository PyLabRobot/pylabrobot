"""Serial transport for legacy Bravo instruments, over PyLabRobot's serial I/O."""

import asyncio
import time
from typing import Optional

from pylabrobot.io.serial import Serial

from ._bridge import _RECEIVE_BUFFER_SIZE, AsyncTransportBase

# Bits on the wire per character at the settings this transport configures: eight
# data bits, one start bit, one stop bit, no parity.
_BITS_PER_CHARACTER = 10

# How long receive() keeps draining once a response has started, expressed in
# character times so that it follows the line speed rather than the hardware.
_DRAIN_CHARACTER_TIMES = 2


class SerialTransport(AsyncTransportBase):
  """A synchronous byte channel over :class:`pylabrobot.io.serial.Serial`.

  ``Serial.read`` takes no timeout argument. It honors whatever read timeout the
  port currently carries, and on expiry returns however many bytes did arrive --
  possibly none -- rather than raising. Both read paths therefore drive that port
  timeout through ``Serial.temporary_timeout``, and turn the short read it can
  return into the behavior :class:`Transport` specifies: ``receive`` yields ``b""``
  when nothing arrived, ``receive_exact`` raises ``TimeoutError``.

  Where ``SocketTransport`` leans on ``Socket.read_exact``, whose timeout applies
  per chunk, ``receive_exact`` here holds a single cumulative deadline across every
  read it issues, so ``timeout`` is the true ceiling on the read itself and the
  outer, future-level bound is only loop-handoff slack.

  Every path to the port -- both reads and the write behind :meth:`send` -- holds
  ``_port_lock`` for the whole of a call, for two reasons that compound. The port's
  read timeout is a single piece of state shared by all of them, so two overlapping
  reads would each install their own timeout over the other's and restore the wrong
  value on the way out. And ``Serial`` runs every port call on one
  ``ThreadPoolExecutor(max_workers=1)``, so a read already occupies that worker for
  the whole of its timeout: an unserialized write would queue behind it and blow
  its own, much smaller, bound against a port that is working perfectly. Serializing
  makes the queueing explicit and charges the wait to the caller that is waiting,
  which is also how a request/response instrument protocol behaves anyway.

  A send the port cannot complete raises pyserial's ``SerialTimeoutException``,
  which :meth:`send` translates to the ``TimeoutError`` :meth:`Transport.send`
  specifies. Only the timeout is translated: a ``SerialException`` from, say, an
  unplugged adapter is not a timeout, and reporting it as one would invite a
  caller to retry against a device that is gone.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    port: str,
    baudrate: int = 9600,
    timeout: float = 2.0,
  ):
    """Create a serial transport for the given port.

    Args:
      human_readable_device_name: Name used in PyLabRobot's I/O logs.
      port: The serial port the instrument is on, e.g. ``/dev/ttyUSB0`` or ``COM3``.
      baudrate: Line speed, in bits per second. Also sets how long :meth:`receive`
        keeps draining a response that has started; see :meth:`_drain_timeout`.
      timeout: Default time to wait for a send to complete, in seconds. Also the
        port's initial read timeout, which every read then overrides for its own
        duration.
    """
    super().__init__(transport_name="serial", endpoint=port)
    self._io = Serial(
      human_readable_device_name=human_readable_device_name,
      port=port,
      baudrate=baudrate,
      write_timeout=timeout,
      timeout=timeout,
    )
    self._baudrate = baudrate
    self._timeout = timeout
    # Built in _open_io rather than here, for two reasons. An asyncio.Lock captures
    # a loop when it is constructed on Python 3.9. And on every version a lock that
    # has been contended on one loop refuses to be contended on another, so a
    # transport stopped and set up again -- or used across two asyncio.run() calls,
    # each of which brings its own loop -- needs a lock belonging to whichever loop
    # owns the connection for that session.
    self._port_lock: Optional[asyncio.Lock] = None

  async def _open_io(self) -> None:
    """Open the serial port."""
    self._port_lock = asyncio.Lock()
    await self._io.setup()

  async def _close_io(self) -> None:
    """Close the serial port."""
    await self._io.stop()

  def _require_port_lock(self) -> asyncio.Lock:
    """The port lock, which exists for as long as a loop owns the connection.

    Returns:
      The lock built by :meth:`_open_io`.

    Raises:
      RuntimeError: If the transport has not been set up.
    """
    lock = self._port_lock
    if lock is None:
      raise RuntimeError("Transport is not set up. Call setup() first.")
    return lock

  def _drain_timeout(self) -> float:
    """How long :meth:`receive` keeps draining once a response has started.

    Draining with the timeout at zero would return only what the port happened to
    hold at that instant, which is a property of the cabling rather than of the
    protocol: a USB adapter with a latency timer hands back a batch, a directly
    attached UART hands back nothing and ``receive`` degrades to a byte per call.
    A couple of character times at the configured line speed is short enough to
    keep ``receive`` prompt and long enough to behave the same either way.

    This bounds the drain as a whole, not the gap between bytes: ``Serial`` exposes
    the port's total read timeout, not pyserial's ``inter_byte_timeout``. It is
    therefore a floor on what a drain collects, not a promise of a whole frame.

    Returns:
      The drain timeout, in seconds.
    """
    return _DRAIN_CHARACTER_TIMES * _BITS_PER_CHARACTER / self._baudrate

  async def _read_available(self, timeout: float) -> bytes:
    """Wait up to ``timeout`` for a first byte, then drain what follows it.

    One blocking read of ``_RECEIVE_BUFFER_SIZE`` bytes would instead wait out the
    full timeout on every call, since a response that large essentially never
    arrives; waiting for a single byte and then draining briefly returns as soon as
    there is anything to return.

    Args:
      timeout: Maximum time to wait for the first byte, in seconds, counted from
        the call rather than from when the lock is won, so that ``timeout`` bounds
        what the caller waits.

    Returns:
      The bytes read, or ``b""`` if none arrived.
    """
    deadline = time.monotonic() + timeout
    async with self._require_port_lock():
      with self._io.temporary_timeout(max(deadline - time.monotonic(), 0.0)):
        first = await self._io.read(num_bytes=1)
      if len(first) == 0:
        return b""
      with self._io.temporary_timeout(self._drain_timeout()):
        rest = await self._io.read(num_bytes=_RECEIVE_BUFFER_SIZE - 1)
    return first + rest

  async def _read_exact(self, num_bytes: int, timeout: float) -> bytes:
    """Read ``num_bytes``, accumulating short reads against one cumulative deadline.

    A read that comes back short is not on its own proof of a timeout, so the
    deadline -- not the length of any single read -- decides when to give up, and
    each further read gets only the time left rather than a fresh ``timeout``.

    Args:
      num_bytes: The exact number of bytes to read.
      timeout: Maximum total time to wait, in seconds, counted from the call
        rather than from when the lock is won, so that ``timeout`` bounds what
        the caller waits.

    Returns:
      Exactly ``num_bytes`` bytes.

    Raises:
      TimeoutError: If the full count does not arrive before the deadline.
    """
    deadline = time.monotonic() + timeout
    data = bytearray()
    async with self._require_port_lock():
      while len(data) < num_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError(
            f"Timeout while reading from serial port after {timeout} seconds, "
            f"{len(data)} of {num_bytes} bytes received"
          )
        with self._io.temporary_timeout(remaining):
          data.extend(await self._io.read(num_bytes=num_bytes - len(data)))
    return bytes(data)

  async def _write(self, data: bytes, timeout: float) -> None:
    """Write ``data``, holding the port for the write.

    Waiting for the port is charged to ``timeout``, as it is on the read paths.
    Unlike them there is no inner timeout left to shrink afterwards -- ``Serial``
    fixes the write timeout when the port is opened -- so the wait is bounded here
    instead, which keeps a busy port reporting this method's own timeout rather
    than the outer bound's generic one.

    Args:
      data: The bytes to write.
      timeout: Maximum time to wait for the port and the write, in seconds.

    Raises:
      TimeoutError: If the port does not come free within ``timeout``.
    """
    deadline = time.monotonic() + timeout
    lock = self._require_port_lock()
    unavailable = f"Timeout while waiting for the serial port after {timeout} seconds"
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise TimeoutError(unavailable)
    try:
      await asyncio.wait_for(lock.acquire(), remaining)
    except asyncio.TimeoutError as exc:
      raise TimeoutError(unavailable) from exc
    try:
      await self._io.write(data)
    finally:
      lock.release()

  def send(self, data: bytes) -> None:
    """Send raw bytes to the device. See :meth:`Transport.send`.

    A write the port cannot complete raises pyserial's ``SerialTimeoutException``,
    which is reported as the ``TimeoutError`` the contract calls for. Any other
    ``OSError``, ``SerialException`` included, is left alone: it is a failure of
    the connection rather than of timing, and the two want different handling.
    """
    try:
      self._run_bounded(self._write(data, self._timeout), self._timeout)
    except TimeoutError:
      # Already the contract's exception, whether it came from the wait for the
      # port or from the outer bound. Taken first because TimeoutError is itself
      # an OSError, and this path must not need pyserial.
      raise
    except OSError as exc:
      # pyserial is installed by construction here: Serial.setup() raises without
      # it, and _run refuses to submit a coroutine before setup() has run.
      import serial

      if isinstance(exc, serial.SerialTimeoutException):
        raise TimeoutError(
          f"Timeout while writing to serial port after {self._timeout} seconds"
        ) from exc
      raise

  def receive(self, timeout: float = 2.0) -> bytes:
    """Receive whatever bytes have arrived, up to 4096 bytes.

    See :meth:`Transport.receive`. Waits up to ``timeout`` for the response to
    begin, then drains for a couple of character times. As on a socket, what comes
    back is not guaranteed to be a whole frame -- a serial line delivers bytes one
    at a time, so a response can easily be split across calls -- and reassembling
    one is the caller's responsibility.
    """
    return self._run_receive(self._read_available(timeout), timeout)

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    """Receive exactly ``num_bytes``. See :meth:`Transport.receive_exact`."""
    return self._run_bounded(self._read_exact(num_bytes, timeout), timeout)
