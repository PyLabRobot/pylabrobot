import asyncio
import ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, cast
from unittest import mock

import anyio
import pytest
from anyio.abc import SocketAttribute, SocketStream

from pylabrobot.concurrency import HasLifespan
from pylabrobot.io import socket as socket_module
from pylabrobot.io.io import ContinuousConnectionIO
from pylabrobot.io.socket import Socket, SocketCommand, SocketValidator
from pylabrobot.testing.concurrency import AnyioTestBase

SocketHandler = Callable[[SocketStream], Awaitable[None]]


@asynccontextmanager
async def _serve_tcp(handler: SocketHandler) -> AsyncIterator[tuple[str, int]]:
  """Serve one test-local TCP listener for the duration of a test."""
  listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
  host, port = cast(tuple[str, int], listener.extra(SocketAttribute.local_address))
  async with listener, anyio.create_task_group() as task_group:
    task_group.start_soon(listener.serve, handler)
    try:
      yield host, port
    finally:
      task_group.cancel_scope.cancel()


def _socket(
  host: str,
  port: int,
  *,
  source_ip: Optional[str] = None,
  ssl_context: Optional[ssl.SSLContext] = None,
  server_hostname: Optional[str] = None,
) -> Socket:
  """Build a socket with short test defaults."""
  return Socket(
    "test socket",
    host,
    port,
    write_timeout=1,
    source_ip=source_ip,
    ssl_context=ssl_context,
    server_hostname=server_hostname,
  )


class TestSocket(AnyioTestBase):
  async def test_send_timeout_cancels_in_flight_send_without_retry(self) -> None:
    io = _socket("192.0.2.10", 7612)
    entered = anyio.Event()
    finished = anyio.Event()
    calls: list[bytes] = []

    async def send(data: bytes) -> None:
      calls.append(data)
      entered.set()
      try:
        await anyio.sleep_forever()
      finally:
        finished.set()

    io._stream = mock.Mock(send=send)
    with pytest.raises(TimeoutError, match="Timeout while writing to socket"):
      await io.write(b"query", timeout=0.01)
    assert entered.is_set() and finished.is_set()
    assert calls == [b"query"]
    with pytest.raises(ConnectionError, match="connection lost"):
      await io.write(b"unsafe retry")

  async def test_send_failure_keeps_original_exception(self) -> None:
    io = _socket("192.0.2.10", 7612)
    failure = ConnectionResetError("peer reset")
    calls: list[bytes] = []

    async def send(data: bytes) -> None:
      calls.append(data)
      raise failure

    io._stream = mock.Mock(send=send)
    with pytest.raises(ConnectionResetError) as error:
      await io.write(b"query")
    assert error.value is failure
    assert calls == [b"query"]

  async def test_default_source_binding_is_forwarded_to_anyio(self) -> None:
    stream = mock.Mock()
    stream.aclose = mock.AsyncMock()
    io = _socket("192.0.2.10", 7612)
    with mock.patch.object(socket_module.anyio, "connect_tcp", return_value=stream) as connect:
      async with io:
        pass
    connect.assert_awaited_once_with(remote_host="192.0.2.10", remote_port=7612, local_host=None)
    stream.aclose.assert_awaited_once()

  async def test_source_binding_is_forwarded_to_anyio(self) -> None:
    async def handler(stream: SocketStream) -> None:
      async with stream:
        await anyio.sleep_forever()

    async with _serve_tcp(handler) as (host, port):
      connect_tcp = anyio.connect_tcp
      with mock.patch.object(socket_module.anyio, "connect_tcp", wraps=connect_tcp) as connect:
        async with _socket(host, port, source_ip="127.0.0.1"):
          pass

      connect.assert_awaited_once_with(
        remote_host=host,
        remote_port=port,
        local_host="127.0.0.1",
      )

  async def test_plain_tcp_lifespan_closes_the_connection(self) -> None:
    accepted = anyio.Event()
    peer_closed = anyio.Event()

    async def handler(stream: SocketStream) -> None:
      async with stream:
        accepted.set()
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()
      peer_closed.set()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      assert isinstance(io, HasLifespan)
      async with io:
        await accepted.wait()
        assert io._stream is not None

      with anyio.fail_after(1):
        await peer_closed.wait()
      assert io._stream is None

  async def test_socket_retains_one_stream_through_multiple_exchanges(self) -> None:
    accepted = 0

    async def handler(stream: SocketStream) -> None:
      nonlocal accepted
      accepted += 1
      async with stream:
        for request, response in ((b"one", b"1"), (b"two", b"2")):
          assert await stream.receive() == request
          await stream.send(response)
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()

    async with _serve_tcp(handler) as (host, port):
      io = Socket("test socket", host, port)
      assert isinstance(io, ContinuousConnectionIO)
      async with io:
        for request, response in ((b"one", b"1"), (b"two", b"2")):
          await io.write(request)
          assert await io.read(1) == response

    assert accepted == 1

  async def test_socket_rejects_io_after_observed_eof(self) -> None:
    accepted = 0

    async def handler(stream: SocketStream) -> None:
      nonlocal accepted
      accepted += 1
      async with stream:
        await stream.send(b"reply")

    async with _serve_tcp(handler) as (host, port):
      io = Socket("test socket", host, port)
      async with io:
        assert await io.read(5) == b"reply"
        with pytest.raises(anyio.EndOfStream):
          await io.read(1)
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.write(b"later")

    assert accepted == 1

  async def test_socket_rejects_io_after_connection_reset(self) -> None:
    stream = mock.Mock()
    stream.receive = mock.AsyncMock(side_effect=ConnectionResetError("peer reset"))
    stream.aclose = mock.AsyncMock()
    io = Socket("test socket", "192.0.2.10", 7612)

    with mock.patch.object(socket_module.anyio, "connect_tcp", return_value=stream):
      async with io:
        with pytest.raises(ConnectionResetError, match="peer reset"):
          await io.read(1)
        with pytest.raises(ConnectionError, match="connection lost"):
          await io.read(1)

    stream.receive.assert_awaited_once()
    stream.aclose.assert_awaited_once()

  async def test_explicit_reconnect_restores_socket_after_observed_loss(self) -> None:
    first = mock.Mock()
    first.receive = mock.AsyncMock(side_effect=ConnectionResetError("peer reset"))
    first.aclose = mock.AsyncMock()
    replacement = mock.Mock()
    replacement.receive = mock.AsyncMock(return_value=b"recovered")
    replacement.aclose = mock.AsyncMock()
    io = Socket("test socket", "192.0.2.10", 7612)

    with mock.patch.object(socket_module.anyio, "connect_tcp", side_effect=[first, replacement]):
      async with io:
        with pytest.raises(ConnectionResetError, match="peer reset"):
          await io.read(1)
        await io.reconnect()
        assert await io.read(9) == b"recovered"

    first.receive.assert_awaited_once()
    first.aclose.assert_awaited_once()
    replacement.aclose.assert_awaited_once()

  async def test_cancelled_tls_entry_closes_the_raw_connection(self) -> None:
    accepted = anyio.Event()
    peer_closed = anyio.Event()
    context = ssl.create_default_context()

    async def handler(stream: SocketStream) -> None:
      async with stream:
        accepted.set()
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()
      peer_closed.set()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port, ssl_context=context, server_hostname="device.test")
      entered = False

      async def cancel_wrap(stream: SocketStream, **kwargs: object) -> SocketStream:
        assert kwargs == {
          "ssl_context": context,
          "hostname": "device.test",
          "standard_compatible": True,
        }
        scope.cancel()
        await anyio.sleep(0)
        raise AssertionError("cancelled TLS entry continued")

      with mock.patch.object(socket_module.TLSStream, "wrap", side_effect=cancel_wrap):
        with anyio.CancelScope() as scope:
          async with io:
            entered = True

      assert not entered
      await accepted.wait()
      with anyio.fail_after(1):
        await peer_closed.wait()
      assert io._stream is None

  async def test_framing_and_capture_commands_are_preserved(self) -> None:
    commands: list[SocketCommand] = []

    async def handler(stream: SocketStream) -> None:
      async with stream:
        assert await stream.receive() == b"ping"
        await stream.send(b"one|line\nuntil<>exacttail")

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      with mock.patch.object(socket_module.capturer, "record", commands.append):
        async with io:
          await io.write(b"ping")
          assert await io.read(3) == b"one"
          assert await io.readline() == b"|line\n"
          assert await io.readuntil(b"<>") == b"until<>"
          assert await io.read_exact(5) == b"exact"
          assert await io.read_until_eof(chunk_size=2) == b"tail"

    assert [(command.action, command.data) for command in commands] == [
      ("write", "70696e67"),
      ("read", "6f6e65"),
      ("readline", "7c6c696e650a"),
      ("readuntil:3c3e", "756e74696c3c3e"),
      ("read_exact", "6578616374"),
      ("read_until_eof", "7461696c"),
    ]

  async def test_eof_behavior_is_preserved(self) -> None:
    async def empty_handler(stream: SocketStream) -> None:
      await stream.aclose()

    async with _serve_tcp(empty_handler) as (host, port):
      io = _socket(host, port)
      async with io:
        with pytest.raises(anyio.EndOfStream):
          await io.read()

    async def line_handler(stream: SocketStream) -> None:
      async with stream:
        await stream.send(b"partial line")

    async with _serve_tcp(line_handler) as (host, port):
      io = _socket(host, port)
      async with io:
        assert await io.readline() == b"partial line"

    async def until_handler(stream: SocketStream) -> None:
      async with stream:
        await stream.send(b"partial separator")

    async with _serve_tcp(until_handler) as (host, port):
      io = _socket(host, port)
      async with io:
        with pytest.raises(asyncio.IncompleteReadError) as error:
          await io.readuntil(b"!")
        assert error.value.partial == b"partial separator"

    async def exact_handler(stream: SocketStream) -> None:
      async with stream:
        await stream.send(b"short")

    async with _serve_tcp(exact_handler) as (host, port):
      io = _socket(host, port)
      async with io:
        with pytest.raises(ConnectionError, match="Connection closed before num_bytes are read"):
          await io.read_exact(6)

  async def test_read_rejects_nonpositive_sizes_without_consuming_data(self) -> None:
    """Only the dedicated EOF operation can request an unbounded read."""
    io = Socket("test socket", "192.0.2.10", 7612)
    io._stream = mock.Mock()
    io._recv_buffer.extend(b"kept")
    for size in (0, -1):
      with pytest.raises(ValueError):
        await io.read(size)
    assert io._recv_buffer == b"kept"

  async def test_explicit_read_timeout_warns_and_is_honored(self) -> None:
    """A migrated caller still gets its requested deadline."""
    io = Socket("test socket", "192.0.2.10", 7612)

    async def wait_for_data(max_bytes: int) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io._stream = mock.Mock(receive=wait_for_data)
    with pytest.warns(DeprecationWarning), pytest.raises(TimeoutError):
      await io.read(1, timeout=0.01)

  async def test_default_read_waits_without_public_deadline(self) -> None:
    """Omitting a timeout does not impose the old 30-second deadline."""
    io = Socket("test socket", "192.0.2.10", 7612)
    assert io._read_timeout is None

  async def test_explicit_constructor_read_timeout_warns(self) -> None:
    """An explicitly supplied legacy deadline remains effective and visible."""
    with pytest.warns(DeprecationWarning):
      io = Socket("test socket", "192.0.2.10", 7612, read_timeout=0.01)

    async def wait_for_data(max_bytes: int) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io._stream = mock.Mock(receive=wait_for_data)
    with pytest.raises(TimeoutError):
      await io.read(1)

  async def test_explicit_none_read_timeout_keeps_constructor_deadline(self) -> None:
    """The legacy override value None still selects the configured constructor timeout."""
    with pytest.warns(DeprecationWarning):
      io = Socket("test socket", "192.0.2.10", 7612, read_timeout=0.01)

    async def wait_for_data(max_bytes: int) -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io._stream = mock.Mock(receive=wait_for_data)
    with pytest.warns(DeprecationWarning), pytest.raises(TimeoutError):
      await io.read(1, timeout=None)

  async def test_cancelled_write_before_dispatch_sends_nothing(self) -> None:
    """Cancellation at the admission checkpoint does not submit a send."""
    io = Socket("test socket", "192.0.2.10", 7612)
    stream = mock.Mock(send=mock.AsyncMock())
    io._stream = stream
    with anyio.CancelScope() as scope:
      scope.cancel()
      await io.write(b"not sent")
    stream.send.assert_not_awaited()

  async def test_cancelled_dispatched_send_finishes_before_caller_exits(self) -> None:
    """A cancelled caller must not abandon an in-flight send."""
    io = Socket("test socket", "192.0.2.10", 7612)
    entered = anyio.Event()
    release = anyio.Event()
    sent: list[bytes] = []

    async def send(data: bytes) -> None:
      entered.set()
      await release.wait()
      sent.append(data)

    io._stream = mock.Mock(send=send)
    exited = anyio.Event()

    async def writer() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        await io.write(b"payload")
      exited.set()

    scopes: list[anyio.CancelScope] = []
    async with anyio.create_task_group() as group:
      group.start_soon(writer)
      await entered.wait()
      scopes[0].cancel()
      await anyio.lowlevel.checkpoint()
      assert not exited.is_set()
      release.set()
      await exited.wait()
    assert sent == [b"payload"]

  async def test_timeout_keeps_bytes_received_by_readuntil(self) -> None:
    release = anyio.Event()

    async def handler(stream: SocketStream) -> None:
      async with stream:
        await stream.send(b"kept")
        await release.wait()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      async with io:
        with pytest.raises(
          TimeoutError, match="Timeout while reading from socket after 0.05 seconds"
        ):
          await io.readuntil(b"!", timeout=0.05)
        assert await io.read(4) == b"kept"
      release.set()

  async def test_non_positive_read_exact_does_not_consume_buffered_data(self) -> None:
    async def handler(stream: SocketStream) -> None:
      async with stream:
        await anyio.sleep_forever()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      async with io:
        io._recv_buffer.extend(b"kept")
        assert await io.read_exact(-1) == b""
        assert await io.read_exact(0) == b""
        assert await io.read_exact(4) == b"kept"

  async def test_cancellation_keeps_bytes_received_by_readuntil(self) -> None:
    release = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    cancelled = anyio.Event()

    async def handler(stream: SocketStream) -> None:
      async with stream:
        await stream.send(b"kept")
        await release.wait()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      async with io:

        async def read_until_cancelled() -> None:
          with anyio.CancelScope() as scope:
            scopes.append(scope)
            await io.readuntil(b"!", timeout=1)
          cancelled.set()

        async with anyio.create_task_group() as task_group:
          task_group.start_soon(read_until_cancelled)
          with anyio.fail_after(1):
            while io._recv_buffer != b"kept":
              await anyio.sleep(0)
          scopes[0].cancel()
          await cancelled.wait()
          assert await io.read(4) == b"kept"
      release.set()

  async def test_read_and_write_locks_are_independent(self) -> None:
    accepted = anyio.Event()
    received = anyio.Event()
    reads: list[bytes] = []

    async def handler(stream: SocketStream) -> None:
      async with stream:
        accepted.set()
        assert await stream.receive() == b"request"
        received.set()
        await stream.send(b"response")

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      async with io:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(self._append_read, io, reads)
          await accepted.wait()
          with anyio.fail_after(1):
            await io.write(b"request")
            await received.wait()

    assert reads == [b"response"]

  async def test_reconnect_closes_and_unpublishes_old_stream_first(self) -> None:
    accepted = 0
    both_accepted = anyio.Event()

    async def handler(stream: SocketStream) -> None:
      nonlocal accepted
      accepted += 1
      if accepted == 2:
        both_accepted.set()
      async with stream:
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      connect_tcp = anyio.connect_tcp
      reconnect_observations = []
      calls = 0

      async def observed_connect(**kwargs: Any) -> SocketStream:
        nonlocal calls
        calls += 1
        if calls == 2:
          reconnect_observations.append((old_socket.fileno(), io._stream))
        return cast(SocketStream, await connect_tcp(**kwargs))

      with mock.patch.object(socket_module.anyio, "connect_tcp", side_effect=observed_connect):
        async with io:
          assert io._stream is not None
          old_stream = io._stream
          old_socket = old_stream.extra(SocketAttribute.raw_socket)
          await io.reconnect()
          assert io._stream is not old_stream
          await both_accepted.wait()

      assert reconnect_observations == [(-1, None)]

  async def test_concurrent_reconnects_are_serialized_without_leaking_streams(self) -> None:
    async def handler(stream: SocketStream) -> None:
      async with stream:
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      connect_tcp = anyio.connect_tcp
      connections: list[SocketStream] = []
      reconnect_started = [anyio.Event(), anyio.Event()]
      reconnect_release = [anyio.Event(), anyio.Event()]
      reconnect_done = [anyio.Event(), anyio.Event()]

      async def observed_connect(**kwargs: Any) -> SocketStream:
        stream = cast(SocketStream, await connect_tcp(**kwargs))
        connections.append(stream)
        reconnect_index = len(connections) - 2
        if reconnect_index >= 0:
          reconnect_started[reconnect_index].set()
          try:
            await reconnect_release[reconnect_index].wait()
          except BaseException:
            with anyio.CancelScope(shield=True):
              await stream.aclose()
            raise
        return stream

      async def reconnect(index: int) -> None:
        await io.reconnect()
        reconnect_done[index].set()

      with mock.patch.object(socket_module.anyio, "connect_tcp", side_effect=observed_connect):
        try:
          async with io:
            async with anyio.create_task_group() as task_group:
              task_group.start_soon(reconnect, 0)
              await reconnect_started[0].wait()
              task_group.start_soon(reconnect, 1)
              await anyio.wait_all_tasks_blocked()
              try:
                assert not reconnect_started[1].is_set()
              finally:
                reconnect_release[0].set()

              await reconnect_done[0].wait()
              await reconnect_started[1].wait()
              assert connections[1].extra(SocketAttribute.raw_socket).fileno() == -1
              reconnect_release[1].set()
              await reconnect_done[1].wait()
              assert io._stream is connections[2]
        finally:
          reconnect_release[0].set()
          reconnect_release[1].set()
          with anyio.CancelScope(shield=True):
            for stream in connections:
              await stream.aclose()

      assert all(stream.extra(SocketAttribute.raw_socket).fileno() == -1 for stream in connections)

  async def test_exit_waits_for_reconnect_and_closes_its_new_stream(self) -> None:
    async def handler(stream: SocketStream) -> None:
      async with stream:
        with pytest.raises(anyio.EndOfStream):
          await stream.receive()

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      connect_tcp = anyio.connect_tcp
      connections: list[SocketStream] = []
      reconnect_started = anyio.Event()
      reconnect_release = anyio.Event()
      reconnect_done = anyio.Event()
      exit_done = anyio.Event()

      async def observed_connect(**kwargs: Any) -> SocketStream:
        stream = cast(SocketStream, await connect_tcp(**kwargs))
        connections.append(stream)
        if len(connections) == 2:
          reconnect_started.set()
          try:
            await reconnect_release.wait()
          except BaseException:
            with anyio.CancelScope(shield=True):
              await stream.aclose()
            raise
        return stream

      async def reconnect() -> None:
        await io.reconnect()
        reconnect_done.set()

      lifespan = io.lifespan()
      with mock.patch.object(socket_module.anyio, "connect_tcp", side_effect=observed_connect):
        await lifespan.__aenter__()

        async def exit_lifespan() -> None:
          await lifespan.__aexit__(None, None, None)
          exit_done.set()

        async with anyio.create_task_group() as task_group:
          task_group.start_soon(reconnect)
          await reconnect_started.wait()
          task_group.start_soon(exit_lifespan)
          await anyio.wait_all_tasks_blocked()
          try:
            assert not exit_done.is_set()
          finally:
            reconnect_release.set()
          await reconnect_done.wait()
          await exit_done.wait()

      assert io._stream is None
      assert all(stream.extra(SocketAttribute.raw_socket).fileno() == -1 for stream in connections)

  async def test_exit_waits_for_active_read_and_rejects_later_reads(self) -> None:
    accepted = anyio.Event()
    send_response = anyio.Event()
    read_finished = anyio.Event()
    exit_finished = anyio.Event()
    reads = []

    async def handler(stream: SocketStream) -> None:
      async with stream:
        accepted.set()
        await send_response.wait()
        await stream.send(b"x")

    async with _serve_tcp(handler) as (host, port):
      io = _socket(host, port)
      lifespan = io.lifespan()
      await lifespan.__aenter__()

      async def read() -> None:
        reads.append(await io.read(1))
        read_finished.set()

      async def exit_lifespan() -> None:
        await lifespan.__aexit__(None, None, None)
        exit_finished.set()

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(read)
        await accepted.wait()
        task_group.start_soon(exit_lifespan)
        await anyio.sleep(0)
        assert not exit_finished.is_set()
        send_response.set()
        await read_finished.wait()
        await exit_finished.wait()

      assert reads == [b"x"]
      with pytest.raises(RuntimeError, match="not set up; call setup\\(\\) first"):
        await io.read(1)

  async def test_validator_uses_the_managed_lifespan(self) -> None:
    validator = SocketValidator(mock.Mock(), "validator", "127.0.0.1", 1)
    assert isinstance(validator, HasLifespan)
    async with validator:
      assert validator.is_active

  async def test_validator_cancellation_and_invalid_size_keep_reply(self) -> None:
    """Replay must not advance before admission or on invalid read arguments."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "socket",
      "device_id": "127.0.0.1:1",
      "action": "read",
      "data": "7265706c79",
    }
    validator = SocketValidator(reader, "validator", "127.0.0.1", 1)
    async with validator:
      with pytest.raises(ValueError):
        await validator.read(0)
      with anyio.CancelScope() as scope:
        scope.cancel()
        await validator.read(5)
      assert await validator.read(5) == b"reply"
    assert reader.next_command.call_count == 1

  async def test_validator_replayed_eof_invalidates_connection(self) -> None:
    """Replay observes EOF once, then rejects later I/O until reconnect."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "socket",
      "device_id": "127.0.0.1:1",
      "action": "read",
      "data": "",
    }
    validator = SocketValidator(reader, "validator", "127.0.0.1", 1)
    async with validator:
      with pytest.raises(anyio.EndOfStream):
        await validator.read(1)
      with pytest.raises(ConnectionError, match="connection lost"):
        await validator.read(1)
      with pytest.raises(ConnectionError, match="connection lost"):
        await validator.write(b"late")
    assert reader.next_command.call_count == 1

  async def test_cancelled_validator_line_read_keeps_replay_command(self) -> None:
    """Framing validators also checkpoint before consuming recorded data."""
    reader = mock.Mock()
    reader.next_command.return_value = {
      "module": "socket",
      "device_id": "127.0.0.1:1",
      "action": "readline",
      "data": "6f6b0a",
    }
    validator = SocketValidator(reader, "validator", "127.0.0.1", 1)
    async with validator:
      with anyio.CancelScope() as scope:
        scope.cancel()
        await validator.readline()
      assert await validator.readline() == b"ok\n"
    assert reader.next_command.call_count == 1

  async def test_validator_reconnect_remains_network_free(self) -> None:
    validator = SocketValidator(mock.Mock(), "validator", "127.0.0.1", 1)
    with mock.patch.object(
      socket_module.anyio,
      "connect_tcp",
      side_effect=AssertionError("validator opened a network connection"),
    ) as connect:
      with pytest.raises(RuntimeError, match="active lifespan"):
        await validator.reconnect()
      async with validator:
        await validator.reconnect()
    connect.assert_not_awaited()

  @staticmethod
  async def _append_read(io: Socket, reads: list[bytes]) -> None:
    reads.append(await io.read_exact(8))
