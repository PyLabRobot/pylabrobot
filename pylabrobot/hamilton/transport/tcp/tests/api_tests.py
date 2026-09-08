"""Public API invariants exercised through the production session and memory I/O."""

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from functools import partial
from unittest.mock import Mock

from pylabrobot.hamilton.nimbus.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.events import EventOverflowError, OverflowPolicy
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.introspection import GetObjectCommand, ObjectInfo
from pylabrobot.hamilton.transport.tcp.messages import CommandResponse, HoiParams, HoiParamsParser
from pylabrobot.hamilton.transport.tcp.packets import Address, HarpPacket, HoiPacket
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.tests.tcp_tests import _MemorySocket, _response, _SessionTest
from pylabrobot.hamilton.transport.tcp.wire_types import I32, Str
from pylabrobot.io.socket import Socket


@dataclass(frozen=True)
class _Query(TCPCommand[int]):
  """A typed status request used to check the full execution path."""

  interface_id = 1
  command_id = 0
  action_code = Hoi2Action.STATUS_REQUEST

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> int:
    """Read a scalar integer response."""
    _, value = HoiParamsParser(data).parse_next()
    return int(value)


class TestRequestAPI(_SessionTest):
  async def test_cancel_during_completed_socket_drain_preserves_uncertainty(self):
    """Replay the real writer's drain race through the full request API."""
    client, io = self.make_client()
    writer = Mock(spec=asyncio.StreamWriter)
    cancellations: list[bool] = []

    def cancel_request(completed: asyncio.Task[None]) -> None:
      """Cancel after drain completes, before its waiter resumes."""
      cancellations.append(task.cancel())

    def reply_after_write(completed: asyncio.Task[None]) -> None:
      """A later response must not turn accepted cancellation into success."""
      io.feed(_response())

    async def drain() -> None:
      """Complete before cancellation reaches the socket's timeout wait."""
      drain_task = asyncio.current_task()
      assert drain_task is not None
      drain_task.add_done_callback(cancel_request)
      write_task = client._session._write_task
      assert write_task is not None
      write_task.add_done_callback(reply_after_write)

    writer.drain.side_effect = drain
    io._writer = writer
    io.write = partial(Socket.write, io)  # type: ignore[method-assign]
    task = asyncio.create_task(client.execute(_Query(Address(1, 1, 257))))
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(cancellations, [True])
    self.assertEqual(client.connection_info.state, SessionState.UNCERTAIN)
    with self.assertRaises(ConnectionError):
      await client.execute(_Query(Address(1, 1, 257)))
    self.assertEqual(writer.write.call_count, 1)
    self.assertIsNone(client._session._write_task)

  async def test_execute_decodes_and_exchange_preserves_frame(self):
    client, io = self.make_client()

    async def respond(request: HarpPacket) -> None:
      """Check the request action and echo its correlation fields."""
      self.assertEqual(HoiPacket.unpack(request.payload).action_code, Hoi2Action.STATUS_REQUEST)
      io.feed(_response(sequence=request.seq, action=Hoi2Action.STATUS_RESPONSE))

    io.on_write = respond
    command = _Query(Address(1, 1, 257))
    result: int = await client.execute(command)
    self.assertEqual(result, 1)
    raw: CommandResponse = await client.exchange(command)
    self.assertEqual(raw.hoi.params, HoiParams().add(1, I32).build())
    self.assertEqual(raw.hoi.action_code, Hoi2Action.STATUS_RESPONSE)

  async def test_request_reuse_has_no_session_stamping(self):
    client, io = self.make_client()
    command = _Query(Address(1, 1, 257))
    original = _Query(Address(1, 1, 257))

    async def respond(request: HarpPacket) -> None:
      """Complete concurrent uses of the same immutable request."""
      io.feed(_response(sequence=request.seq))

    io.on_write = respond
    self.assertEqual(await asyncio.gather(client.execute(command), client.execute(command)), [1, 1])
    self.assertEqual(command, original)
    self.assertEqual([packet.seq for packet in io.writes], [1, 2])
    with self.assertRaises(FrozenInstanceError):
      command.dest = Address(1, 1, 999)  # type: ignore[misc]

  async def test_discovery_returns_declared_response(self):
    client, io = self.make_client()
    params = HoiParams().add("Root", Str).add("v1", Str).u32(6).u16(2).build()

    async def respond(request: HarpPacket) -> None:
      """Return a real discovery payload."""
      io.feed(_response(sequence=request.seq, params=params))

    io.on_write = respond
    result: GetObjectCommand.Response = await client.execute(GetObjectCommand(Address(1, 1, 257)))
    self.assertEqual(result, GetObjectCommand.Response("Root", "v1", 6, 2))

  async def test_empty_typed_response_is_decode_error_without_poisoning_connection(self):
    client, io = self.make_client()

    async def respond(request: HarpPacket) -> None:
      """Provide a terminal frame with missing required fields."""
      io.feed(_response(sequence=request.seq, params=b""))

    io.on_write = respond
    with self.assertRaises(ValueError):
      await client.execute(GetObjectCommand(Address(1, 1, 257)))
    self.assertEqual(client._session.state, SessionState.READY)

  async def test_socket_and_raw_io_are_not_public(self):
    client, io = self.make_client()
    with self.assertRaises(AttributeError):
      client.io  # type: ignore[attr-defined]
    with self.assertRaises(AttributeError):
      client.write(b"x")  # type: ignore[attr-defined]
    with self.assertRaises(AttributeError):
      client.read(1)  # type: ignore[attr-defined]
    with self.assertRaises(AttributeError):
      client.read_exact(1)  # type: ignore[attr-defined]
    info = client.connection_info
    with self.assertRaises(FrozenInstanceError):
      info.port = 999  # type: ignore[misc]
    await client.stop()
    self.assertEqual(info.state, SessionState.READY)
    self.assertEqual(client.connection_info.state, SessionState.CLOSED)
    self.assertEqual(io.writes, [])


class TestDiscoveryLifetime(_SessionTest):
  async def test_retained_facade_rejects_cache_hits_after_reconnect(self):
    client, old_io = self.make_client()
    old = client._session
    intro = client.introspection
    address = Address(1, 1, 257)
    old.registry.register("Root", ObjectInfo("Root", "", 0, 0, address))
    intro._method_table_by_address[address] = []
    self.assertEqual(await intro.resolve_path("Root"), address)
    await client.stop()
    fresh_io = _MemorySocket()
    fresh = TCPSession(fresh_io)
    fresh.client_address = Address(2, 2, 65535)
    fresh.state = SessionState.CONNECTING
    fresh.start_reader()
    client._session = fresh
    for operation in (
      intro.resolve_path("Root"),
      intro.ensure_method_table(address),
      intro.get_object(address),
    ):
      with self.assertRaises(ConnectionError):
        await operation
    self.assertIsNot(client.introspection, intro)
    self.assertIsNot(client.registry, old.registry)
    self.assertEqual(old_io.writes, [])
    self.assertEqual(fresh_io.writes, [])

  async def test_uncertain_session_rejects_cached_discovery(self):
    client, _ = self.make_client()
    intro = client.introspection
    address = Address(1, 1, 257)
    intro._method_table_by_address[address] = []
    with self.assertRaises(asyncio.TimeoutError):
      await client.exchange(_Query(address), read_timeout=0)
    with self.assertRaises(ConnectionError):
      await intro.ensure_method_table(address)


class TestFirmwareErrorAPI(_SessionTest):
  async def test_errors_preserve_payload_and_do_not_enrich_or_recurse(self):
    client, io = self.make_client()
    payload = HoiParams().add("0x0001.0x0001.0x0101:0x01,0x0006,0x0F08", Str).build()

    async def respond(request: HarpPacket) -> None:
      """Reject every request, including any accidental diagnostic queries."""
      io.feed(_response(sequence=request.seq, action=Hoi2Action.STATUS_EXCEPTION, params=payload))

    io.on_write = respond
    for _ in range(2):
      with self.assertRaises(HoiError) as ctx:
        await client.execute(_Query(Address(1, 1, 257)), read_timeout=0.1)
      self.assertEqual(ctx.exception.raw_response, payload)
      self.assertEqual(ctx.exception.action, Hoi2Action.STATUS_EXCEPTION)
      self.assertEqual(ctx.exception.entries[0].result, 0x0F08)
    self.assertEqual(len(io.writes), 2)
    raw = await client.exchange(_Query(Address(1, 1, 257)))
    self.assertEqual(raw.hoi.params, payload)
    self.assertEqual(raw.hoi.action_code, Hoi2Action.STATUS_EXCEPTION)

  async def test_empty_firmware_exception_stays_structured(self):
    client, io = self.make_client()

    async def respond(request: HarpPacket) -> None:
      """Return an invalid-action error without diagnostic entries."""
      io.feed(
        _response(sequence=request.seq, action=Hoi2Action.INVALID_ACTION_RESPONSE, params=b"")
      )

    io.on_write = respond
    with self.assertRaises(HoiError) as ctx:
      await client.execute(_Query(Address(1, 1, 257)))
    self.assertEqual(ctx.exception.entries, [])
    self.assertEqual(ctx.exception.action, Hoi2Action.INVALID_ACTION_RESPONSE)
    self.assertEqual(ctx.exception.raw_response, b"")

  async def test_static_error_table_keys_use_interface_id(self):
    client, io = self.make_client()
    client._session._error_codes = NIMBUS_ERROR_CODES

    for result, description in (
      (0x0F4E, "Tip Detected Not Correct Tip"),
      (0x0F4B, "No Tip Picked Up"),
    ):
      payload = HoiParams().add(f"0x0001.0x0001.0x0110:0x01,0x0006,0x{result:04X}", Str).build()

      async def respond(request: HarpPacket) -> None:
        """Provide a captured Nimbus error shape."""
        io.feed(_response(sequence=request.seq, action=Hoi2Action.STATUS_EXCEPTION, params=payload))

      io.on_write = respond
      with self.assertRaises(HoiError) as ctx:
        await client.execute(_Query(Address(1, 1, 257)))
      self.assertIn(description, str(ctx.exception))
    self.assertEqual(len(io.writes), 2)


class TestAsyncEventAPI(_SessionTest):
  async def test_events_arrive_between_commands_and_context_unsubscribes(self):
    client, io = self.make_client()
    async with client.events() as events:
      io.feed(_response(action=Hoi2Action.EVENT))
      event = await asyncio.wait_for(events.__anext__(), 1)
      self.assertEqual(event.hoi.action_code, Hoi2Action.EVENT)
    self.assertEqual(client._session._subscriptions, set())
    with self.assertRaises(StopAsyncIteration):
      await events.__anext__()

  async def test_slow_subscriber_overflow_is_explicit_and_does_not_block_responses(self):
    client, io = self.make_client()
    slow = client.events(capacity=1)
    fast = client.events(capacity=3)
    task = self.send(client)
    await self.wait_until(lambda: len(io.writes) == 1)
    for _ in range(2):
      io.feed(_response(action=Hoi2Action.EVENT))
    io.feed(_response())
    await task
    with self.assertRaises(EventOverflowError):
      await slow.__anext__()
    self.assertEqual((await fast.__anext__()).hoi.action_code, Hoi2Action.EVENT)
    self.assertEqual((await fast.__anext__()).hoi.action_code, Hoi2Action.EVENT)
    self.assertTrue(client.is_connected)

  async def test_drop_policies_report_loss_and_preserve_expected_event(self):
    client, _ = self.make_client()
    policies: tuple[tuple[OverflowPolicy, int], ...] = (("drop_oldest", 2), ("drop_newest", 1))
    for policy, expected in policies:
      async with client.events(capacity=1, overflow=policy) as events:
        for seq in (1, 2):
          client._session.deliver(_response(sequence=seq, action=Hoi2Action.EVENT))
        self.assertEqual(events.dropped, 1)
        self.assertEqual((await events.__anext__()).harp.seq, expected)

  async def test_stop_wakes_consumer_and_closes_subscription(self):
    client, _ = self.make_client()
    events = client.events()
    waiting = asyncio.create_task(events.__anext__())
    await asyncio.sleep(0)
    await client.stop()
    with self.assertRaises(StopAsyncIteration):
      await waiting
    self.assertEqual(client._session._subscriptions, set())

  async def test_reader_failure_wakes_consumer(self):
    client, io = self.make_client()
    events = client.events()
    waiting = asyncio.create_task(events.__anext__())
    io.stream.set_exception(ConnectionResetError("reset"))
    with self.assertRaises(ConnectionResetError):
      await asyncio.wait_for(waiting, 1)

  async def test_request_timeout_closes_subscriptions(self):
    client, _ = self.make_client()
    events = client.events()
    with self.assertRaises(asyncio.TimeoutError):
      await client.exchange(_Query(Address(1, 1, 257)), read_timeout=0)
    with self.assertRaises(ConnectionError):
      await events.__anext__()

  async def test_cancelled_consumer_can_resume(self):
    client, io = self.make_client()
    async with client.events() as events:
      waiting = asyncio.create_task(events.__anext__())
      await asyncio.sleep(0)
      waiting.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await waiting
      io.feed(_response(action=Hoi2Action.EVENT))
      self.assertEqual(
        (await asyncio.wait_for(events.__anext__(), 1)).hoi.action_code, Hoi2Action.EVENT
      )
