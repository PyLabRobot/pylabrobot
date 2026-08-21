import asyncio
import time
import unittest
from typing import List, Optional, Tuple

from pylabrobot.agilent.bravo.transport._bridge import AsyncTransportBase
from pylabrobot.agilent.bravo.transport.base_tests import TransportContractTests
from pylabrobot.agilent.bravo.transport.serial import SerialTransport
from pylabrobot.io.serial import HAS_SERIAL, Serial


class _FakePort:
  """The pyserial port object that Serial's timeout helpers read and write through."""

  def __init__(self, timeout: float):
    self.timeout = timeout


class FakeSerial(Serial):
  """A Serial whose port is a scheduled byte queue rather than a device.

  ``read`` reproduces pyserial's contract, which is what the transport is written
  against: block until ``num_bytes`` have arrived or the port's current timeout
  elapses, then return whatever did arrive -- possibly nothing -- instead of
  raising. Timeouts are read through the inherited ``get_read_timeout``, so
  ``temporary_timeout`` drives this fake exactly as it drives a real port.

  ``echo`` makes the port answer a write with the same bytes, the way the socket
  tests' EchoServer does, which is what the shared contract suite needs of a
  device. ``max_read_size`` caps how much one read returns, so a caller that must
  tolerate short reads can be exercised deliberately.
  """

  _POLL_INTERVAL_S = 0.002

  def __init__(
    self,
    timeout: float = 1.0,
    max_read_size: Optional[int] = None,
    echo: bool = False,
  ):
    super().__init__(
      human_readable_device_name="fake bravo",
      port="/dev/fake",
      timeout=timeout,
    )
    # A stand-in for the pyserial port, which is never opened here.
    self._ser = _FakePort(timeout)  # type: ignore[assignment]
    self._max_read_size = max_read_size
    self._echo = echo
    self._buffered = bytearray()
    self._scheduled: List[Tuple[float, bytes]] = []
    self.written: List[bytes] = []
    self.read_sizes: List[int] = []
    self.read_timeouts: List[float] = []
    # Port calls in the order they finished, which is what tells a write that
    # waited its turn from one that cut in front of a read.
    self.completed: List[str] = []
    self.write_error: Optional[BaseException] = None

  def arrive(self, data: bytes, after: float = 0.0) -> None:
    """Make data readable ``after`` seconds from now."""
    self._scheduled.append((time.monotonic() + after, data))

  def reset(self) -> None:
    """Forget every call and every byte, so one fake can serve two sessions."""
    self._buffered.clear()
    self._scheduled.clear()
    self.written.clear()
    self.read_sizes.clear()
    self.read_timeouts.clear()
    self.completed.clear()

  def _collect_arrived(self) -> None:
    now = time.monotonic()
    while self._scheduled and self._scheduled[0][0] <= now:
      self._buffered.extend(self._scheduled.pop(0)[1])

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes) -> None:
    if self.write_error is not None:
      raise self.write_error
    self.written.append(data)
    self.completed.append("write")
    if self._echo:
      self.arrive(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    self.read_sizes.append(num_bytes)
    self.read_timeouts.append(self.get_read_timeout())
    wanted = num_bytes if self._max_read_size is None else min(num_bytes, self._max_read_size)
    deadline = time.monotonic() + self.get_read_timeout()
    out = bytearray()
    while True:
      self._collect_arrived()
      take = min(wanted - len(out), len(self._buffered))
      if take > 0:
        out.extend(self._buffered[:take])
        del self._buffered[:take]
      remaining = deadline - time.monotonic()
      if len(out) >= wanted or remaining <= 0:
        self.completed.append("read")
        return bytes(out)
      await asyncio.sleep(min(self._POLL_INTERVAL_S, remaining))


async def _wait_for_first_read(io: FakeSerial) -> None:
  """Block until a read has actually reached the port.

  Waiting on the port itself, rather than sleeping a fixed interval and hoping,
  is what keeps the overlap these tests depend on from quietly stopping under
  load, which is the condition where the lock matters most.
  """
  deadline = time.monotonic() + 2.0
  while not io.read_sizes:
    if time.monotonic() > deadline:
      raise AssertionError("the first read never reached the port")
    await asyncio.sleep(0.005)


async def _overlapping_reads(transport: SerialTransport, io: FakeSerial) -> List[object]:
  """Run a receive and a receive_exact that are certain to overlap on the port.

  Returns the two outcomes: ``b""`` from the receive, and the ``TimeoutError``
  the starved receive_exact raises.
  """
  first = asyncio.ensure_future(asyncio.to_thread(transport.receive, 0.30))
  await _wait_for_first_read(io)
  second = asyncio.ensure_future(asyncio.to_thread(transport.receive_exact, 2, 0.60))
  return list(await asyncio.gather(first, second, return_exceptions=True))


class SerialTransportTests(TransportContractTests, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.io = FakeSerial(echo=True)
    self.transport = await self._connected(self.io)

  async def _connected(self, io: FakeSerial, baudrate: int = 9600) -> SerialTransport:
    # Built through the real constructor, so the keyword arguments it passes to
    # Serial stay honest, then pointed at the fake because there is no port here.
    transport = SerialTransport(
      human_readable_device_name="fake bravo",
      port="/dev/fake",
      baudrate=baudrate,
    )
    transport._io = io
    await transport.setup()
    self.addAsyncCleanup(transport.stop)
    return transport

  async def connected_transport(self) -> AsyncTransportBase:
    return self.transport

  async def chunked_transport(self) -> AsyncTransportBase:
    return await self._connected(FakeSerial(echo=True, max_read_size=3))

  def unconnected_transport(self) -> AsyncTransportBase:
    transport = SerialTransport(human_readable_device_name="unconnected", port="/dev/fake")
    transport._io = FakeSerial()
    return transport

  def _send_failure(self, data: bytes) -> BaseException:
    """Send from a worker thread and hand back whatever it raised.

    Returning the exception rather than letting it travel out of the thread keeps
    its ``__cause__``, for the reason ``AsyncTransportBase._run`` documents.
    """
    try:
      self.transport.send(data)
    except BaseException as exc:  # noqa: BLE001 - the exception is the assertion
      return exc
    raise AssertionError("send did not raise")

  async def test_receive_returns_before_the_timeout_elapses(self):
    # The port has no way to say "that is the whole response", so a single
    # blocking read of the full buffer size would wait out the timeout on every
    # call. receive() waits out the timeout only for the first byte, then drains
    # briefly, and so comes back as soon as there is something to hand over.
    self.io.arrive(b"hi")
    start = time.monotonic()
    result = await asyncio.to_thread(self.transport.receive, 2.0)
    elapsed = time.monotonic() - start
    self.assertEqual(result, b"hi")
    self.assertEqual(self.io.read_sizes[0], 1)
    self.assertEqual(len(self.io.read_timeouts), 2)
    self.assertAlmostEqual(self.io.read_timeouts[0], 2.0, places=1)
    self.assertGreater(self.io.read_timeouts[1], 0.0)
    self.assertLess(self.io.read_timeouts[1], 0.1)
    self.assertLess(elapsed, 1.5)

  async def test_drain_timeout_is_derived_from_the_line_speed(self):
    # Draining with the timeout at zero would return only what the port happened
    # to hold at that instant, which is a property of the cabling: an adapter
    # with a latency timer batches, a directly attached UART does not. A couple
    # of character times makes the drain the protocol's business instead, so it
    # is nonzero everywhere and halves when the line runs twice as fast.
    slow = FakeSerial()
    slow_transport = await self._connected(slow, baudrate=9600)
    fast = FakeSerial()
    fast_transport = await self._connected(fast, baudrate=19200)

    slow.arrive(b"hi")
    fast.arrive(b"hi")
    await asyncio.to_thread(slow_transport.receive, 2.0)
    await asyncio.to_thread(fast_transport.receive, 2.0)

    self.assertGreater(slow.read_timeouts[1], 0.0)
    self.assertAlmostEqual(slow.read_timeouts[1], 0.00208, places=5)
    self.assertAlmostEqual(slow.read_timeouts[1], 2 * fast.read_timeouts[1], places=6)

  async def test_receive_exact_short_reads_ask_only_for_what_is_outstanding(self):
    # A port that hands back less than was asked for on each read: receive_exact
    # must keep reading until the count is met, and ask each time only for the
    # bytes still missing.
    io = FakeSerial(max_read_size=3)
    transport = await self._connected(io)
    io.arrive(b"12345678")

    result = await asyncio.to_thread(transport.receive_exact, 8, 1.0)

    self.assertEqual(result, b"12345678")
    self.assertEqual(io.read_sizes, [8, 5, 2])

  async def test_receive_exact_waits_for_bytes_that_arrive_over_time(self):
    self.io.arrive(b"12")
    self.io.arrive(b"34", after=0.05)
    self.io.arrive(b"5678", after=0.1)

    start = time.monotonic()
    result = await asyncio.to_thread(self.transport.receive_exact, 8, 1.0)
    elapsed = time.monotonic() - start

    self.assertEqual(result, b"12345678")
    self.assertGreater(elapsed, 0.1)

  async def test_receive_exact_deadline_is_cumulative_across_reads(self):
    # A port that returns one byte at a time, with the third byte never coming.
    # Each read must be given only the time still left before the deadline, so
    # that `timeout` bounds the whole call rather than every read separately.
    io = FakeSerial(max_read_size=1)
    transport = await self._connected(io)
    io.arrive(b"1")
    io.arrive(b"2", after=0.15)

    start = time.monotonic()
    with self.assertRaises(TimeoutError) as ctx:
      await asyncio.to_thread(transport.receive_exact, 4, 0.3)
    elapsed = time.monotonic() - start

    message = str(ctx.exception)
    self.assertIn("0.3 seconds", message)
    self.assertIn("2 of 4 bytes received", message)
    self.assertNotIn("did not complete within", message)
    # Granted timeouts shrink as the deadline approaches; they never start over.
    self.assertLess(io.read_timeouts[-1], io.read_timeouts[0])
    self.assertGreater(elapsed, 0.25)

  async def test_overlapping_reads_do_not_corrupt_the_port_timeout(self):
    # The port's read timeout is one piece of state that every reader installs
    # over and restores. Two reads running at once, each wrapping its own
    # temporary_timeout around an await, would capture each other's value as the
    # one to restore: the port would be left holding a read's timeout instead of
    # its own, and the second read would be handed a fresh 0.60 seconds rather
    # than what was left of it. The lock is what stops both.
    original = self.io.get_read_timeout()

    outcomes = await _overlapping_reads(self.transport, self.io)

    self.assertEqual(outcomes[0], b"")
    self.assertIsInstance(outcomes[1], TimeoutError)
    self.assertEqual(self.io.get_read_timeout(), original)
    # The second read waited its turn, so it got what was left of its 0.60
    # seconds after the first read's 0.30, not the whole of it back again.
    self.assertEqual(len(self.io.read_timeouts), 2)
    self.assertLess(self.io.read_timeouts[1], 0.45)

  async def test_port_lock_belongs_to_the_loop_that_owns_the_connection(self):
    # Built where no event loop is running, the way a script that creates its
    # transports before starting one does, and set up afterwards on the loop that
    # goes on to own the connection. On Python 3.9 an asyncio.Lock captures a loop
    # the moment it is constructed, so a lock built beside the transport would
    # belong to another loop or to none -- which nothing but contention, where the
    # lock has to suspend a waiter, would ever surface.
    io = FakeSerial()

    def build() -> SerialTransport:
      transport = SerialTransport(human_readable_device_name="fake bravo", port="/dev/fake")
      transport._io = io
      return transport

    transport = await asyncio.to_thread(build)
    await transport.setup()
    self.addAsyncCleanup(transport.stop)

    outcomes = await _overlapping_reads(transport, io)

    self.assertEqual(outcomes[0], b"")
    self.assertIsInstance(outcomes[1], TimeoutError)
    self.assertEqual(io.get_read_timeout(), 1.0)

  async def test_send_waits_for_a_read_to_release_the_port(self):
    # Serial runs every port call on one worker thread, so a write issued while a
    # read holds that worker queues behind it whether or not this transport says
    # so. Saying so is what lets the write be charged for the wait and answer
    # within its own budget, instead of the outer bound firing against a port that
    # is working perfectly. The write must take its turn, and be seen to.
    reading = asyncio.ensure_future(asyncio.to_thread(self.transport.receive, 0.30))
    await _wait_for_first_read(self.io)

    await asyncio.to_thread(self.transport.send, b"ping")

    self.assertEqual(await reading, b"")
    self.assertEqual(self.io.written, [b"ping"])
    # The order the port saw them finish in, which is what "took its turn" means
    # and is decided by the lock rather than by the clock. Timing how long the
    # send waited would instead measure how much of the read's 0.30 seconds had
    # already elapsed before the wait could be timed at all.
    self.assertEqual(self.io.completed, ["read", "write"])

  @unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
  async def test_send_reports_a_write_timeout_as_timeout_error(self):
    # Transport.send promises TimeoutError, so pyserial's SerialTimeoutException
    # -- which is an OSError, not a TimeoutError -- has to be reported as one, or
    # a caller could not handle a send timeout the same way across transports.
    import serial

    cause = serial.SerialTimeoutException("write timeout")
    self.io.write_error = cause

    raised = await asyncio.to_thread(self._send_failure, b"ping")

    self.assertIsInstance(raised, TimeoutError)
    self.assertIn("Timeout while writing to serial port", str(raised))
    self.assertIs(raised.__cause__, cause)

  @unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
  async def test_send_leaves_a_non_timeout_serial_failure_alone(self):
    # A port that has gone away is not a timeout. Reporting it as one would tell
    # a caller to retry against a device that is gone.
    import serial

    cause = serial.SerialException("device disconnected")
    self.io.write_error = cause

    raised = await asyncio.to_thread(self._send_failure, b"ping")

    self.assertIs(raised, cause)


class SerialTransportAcrossLoopsTests(unittest.TestCase):
  def test_port_lock_is_rebuilt_for_each_loop_that_owns_the_connection(self):
    # asyncio.run() brings its own loop and disposes of it afterwards, so a
    # transport driven by two of them is set up on a different loop each time. A
    # lock that outlived the first loop would be usable but not contendable on
    # the second, which is the state these overlapping reads walk into.
    io = FakeSerial()
    transport = SerialTransport(human_readable_device_name="fake bravo", port="/dev/fake")
    transport._io = io

    async def session() -> List[object]:
      await transport.setup()
      try:
        return await _overlapping_reads(transport, io)
      finally:
        await transport.stop()

    for _ in range(2):
      io.reset()
      outcomes = asyncio.run(session())
      self.assertEqual(outcomes[0], b"")
      self.assertIsInstance(outcomes[1], TimeoutError)
      self.assertEqual(io.get_read_timeout(), 1.0)


if __name__ == "__main__":
  unittest.main()
