import asyncio
import unittest
from typing import TYPE_CHECKING, Tuple

from pylabrobot.agilent.bravo.transport._bridge import _RECEIVE_BUFFER_SIZE, AsyncTransportBase
from pylabrobot.agilent.bravo.transport.base import Transport


class ConcreteTransport(Transport):
  def __init__(self):
    self.sent = b""

  def send(self, data: bytes) -> None:
    self.sent += data

  def receive(self, timeout: float = 2.0) -> bytes:
    return b"ok"

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    return b"o" * num_bytes

  @property
  def is_connected(self) -> bool:
    return True


class TransportInterfaceTests(unittest.TestCase):
  def test_concrete_subclass_satisfies_interface(self):
    t = ConcreteTransport()
    t.send(b"hello")
    self.assertEqual(t.sent, b"hello")
    self.assertEqual(t.receive(), b"ok")
    self.assertEqual(t.receive_exact(3), b"ooo")
    self.assertTrue(t.is_connected)

  def test_incomplete_subclass_cannot_be_instantiated(self):
    class Incomplete(Transport):
      def send(self, data: bytes) -> None:
        pass

    with self.assertRaises(TypeError):
      Incomplete()  # type: ignore[abstract]


if TYPE_CHECKING:
  # Typing sees a TestCase, so the assertions below resolve; at runtime the base
  # is object, which keeps this class out of collection and out of the run.
  _ContractTestsBase = unittest.IsolatedAsyncioTestCase
else:
  _ContractTestsBase = object


class TransportContractTests(_ContractTestsBase):
  """What a transport owes its callers, whatever I/O it is built on.

  A concrete transport's test class mixes this in alongside
  ``unittest.IsolatedAsyncioTestCase`` and supplies the three hooks below. The
  point is that these are answers the :class:`Transport` contract requires -- plus
  the setup/stop lifecycle :class:`AsyncTransportBase` adds around it -- and not
  observations about one transport, so a transport added later inherits them
  rather than reimplementing them and getting one subtly wrong. Tests that turn
  on how a particular transport works belong beside that transport instead.
  """

  async def connected_transport(self) -> AsyncTransportBase:
    """A set-up transport whose device echoes back whatever is sent to it.

    Cleanup is the hook's responsibility.
    """
    raise NotImplementedError

  async def chunked_transport(self) -> AsyncTransportBase:
    """A set-up, echoing transport whose device answers in several short reads.

    Cleanup is the hook's responsibility.
    """
    raise NotImplementedError

  def unconnected_transport(self) -> AsyncTransportBase:
    """A transport that has not been set up."""
    raise NotImplementedError

  async def test_send_before_setup_raises(self):
    transport = self.unconnected_transport()
    with self.assertRaises(RuntimeError):
      await asyncio.to_thread(transport.send, b"x")

  async def test_is_connected_reflects_lifecycle(self):
    transport = await self.connected_transport()
    self.assertTrue(transport.is_connected)
    await transport.stop()
    self.assertFalse(transport.is_connected)
    await transport.setup()
    self.assertTrue(transport.is_connected)

  async def test_send_reaches_the_device(self):
    transport = await self.connected_transport()

    def blocking_roundtrip() -> bytes:
      transport.send(b"ping")
      return transport.receive_exact(4)

    self.assertEqual(await asyncio.to_thread(blocking_roundtrip), b"ping")

  async def test_receive_returns_what_the_device_sent(self):
    transport = await self.connected_transport()

    def blocking_roundtrip() -> bytes:
      transport.send(b"pong")
      return transport.receive()

    self.assertEqual(await asyncio.to_thread(blocking_roundtrip), b"pong")

  async def test_receive_returns_empty_bytes_on_timeout(self):
    transport = await self.connected_transport()
    self.assertEqual(await asyncio.to_thread(transport.receive, 0.2), b"")

  async def test_receive_exact_assembles_across_reads(self):
    transport = await self.chunked_transport()

    def blocking_roundtrip() -> bytes:
      transport.send(b"12345678")
      return transport.receive_exact(8)

    self.assertEqual(await asyncio.to_thread(blocking_roundtrip), b"12345678")

  async def test_drain_returns_zero_on_an_idle_connection(self):
    # Nothing pending is the ordinary case for a drain, not a failure: recovery
    # code calls it without knowing whether the device left anything behind.
    transport = await self.connected_transport()
    self.assertEqual(await asyncio.to_thread(transport.drain), 0)

  async def test_drain_discards_everything_pending_and_leaves_the_device_readable(self):
    # Stale bytes amounting to more than one receive() can return, so emptying
    # the buffer genuinely takes more than one read. They are sent as a single
    # burst rather than dribbled out over time, because a device that is still
    # sending is one no timeout-bounded drain can promise to have caught up with
    # -- that is what the budget is for -- whereas bytes already sitting in the
    # buffer are exactly what this is meant to clear.
    transport = await self.connected_transport()
    stale = b"x" * (_RECEIVE_BUFFER_SIZE + 200)

    def blocking_call() -> Tuple[int, bytes]:
      transport.send(stale)
      discarded = transport.drain()
      transport.send(b"fresh")
      return discarded, transport.receive_exact(5)

    discarded, framed = await asyncio.to_thread(blocking_call)

    # The half that matters: whatever the count says, the next framed read must
    # see its own frame and not the tail of the one that was discarded.
    self.assertEqual(framed, b"fresh")
    self.assertEqual(discarded, len(stale))

  async def test_receive_exact_timeout_names_the_read_not_the_outer_bound(self):
    transport = await self.connected_transport()

    def blocking_call() -> bytes:
      transport.send(b"ab")
      return transport.receive_exact(4, timeout=0.2)

    with self.assertRaises(TimeoutError) as ctx:
      await asyncio.to_thread(blocking_call)

    # The read's own timeout, not the outer future-level bound. Pinned by message
    # because class cannot tell them apart: concurrent.futures.TimeoutError is the
    # builtin TimeoutError from Python 3.11 onward.
    message = str(ctx.exception)
    self.assertIn("0.2 seconds", message)
    self.assertNotIn("did not complete within", message)


if __name__ == "__main__":
  unittest.main()
