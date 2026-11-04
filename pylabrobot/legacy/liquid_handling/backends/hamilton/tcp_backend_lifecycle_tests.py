"""Lifecycle tests for the legacy Hamilton direct-read TCP backend."""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, cast

import anyio
import pytest
from anyio.abc import SocketAttribute, SocketStream

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.socket import Socket
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.messages import CommandResponse
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.packets import (
  Address,
  HarpPacket,
  HoiPacket,
  IpPacket,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonProtocol,
  Hoi2Action,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp_backend import HamiltonTCPBackend
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.testing.concurrency import AnyioTestBase


class RecordingSocket(Socket):
  """Record socket ownership, reconnects, writes, and exact reads."""

  def __init__(
    self,
    events: list[str],
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
    reconnect_effects: Optional[list[Optional[BaseException]]] = None,
    response: bytes = b"",
    block_read: bool = False,
    block_write: bool = False,
  ) -> None:
    super().__init__(human_readable_device_name="recording", host="ignored", port=0)
    self.events = events
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.reconnect_effects = reconnect_effects or []
    self.response = bytearray(response)
    self.block_read = block_read
    self.block_write = block_write
    self.write_started = anyio.Event()
    self.write_cancellation: Optional[BaseException] = None
    self.connection_active = False
    self.connection_generation = 0

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingSocket does not accept lifespan options")
    self.events.append("socket.enter")
    if self.entry_error is not None:
      raise self.entry_error
    self.connection_active = True
    self.connection_generation += 1
    stack.push_shielded_async_callback(self._exit_socket)

  async def _exit_socket(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("socket.exit")
    self.connection_active = False
    if self.exit_error is not None:
      raise self.exit_error

  async def reconnect(self) -> None:
    if not self.is_active:
      raise RuntimeError("reconnect outside socket lifespan")
    self.events.append("socket.reconnect")
    self.connection_active = False
    effect = self.reconnect_effects.pop(0) if self.reconnect_effects else None
    await anyio.lowlevel.checkpoint()
    if effect is not None:
      raise effect
    self.connection_active = True
    self.connection_generation += 1

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    del data, timeout
    if not self.connection_active:
      raise RuntimeError("write outside socket lifespan")
    self.events.append(f"write:{self.connection_generation}")
    if self.block_write:
      self.write_started.set()
      try:
        await anyio.sleep_forever()
      except anyio.get_cancelled_exc_class() as error:
        self.write_cancellation = error
        raise
    await anyio.lowlevel.checkpoint()

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    del timeout
    if not self.connection_active:
      raise RuntimeError("read outside socket lifespan")
    self.events.append(f"read:{num_bytes}")
    if self.block_read:
      await anyio.sleep_forever()
    await anyio.lowlevel.checkpoint()
    data = bytes(self.response[:num_bytes])
    del self.response[:num_bytes]
    return data


class RecordingHamiltonTCPBackend(HamiltonTCPBackend):
  """Concrete protocol test backend with individually recorded handshake phases."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_at: Optional[str] = None,
    block_at: Optional[str] = None,
    phase_started: Optional[anyio.Event] = None,
    socket_kwargs: Optional[dict[str, Any]] = None,
    max_reconnect_attempts: int = 3,
    recover_after_backoff: bool = False,
  ) -> None:
    super().__init__(host="ignored", port=0, max_reconnect_attempts=max_reconnect_attempts)
    self.events = events
    self.fail_at = fail_at
    self.block_at = block_at
    self.phase_started = phase_started
    self.backoff_delays: list[float] = []
    self.recover_after_backoff = recover_after_backoff
    self.io = cast(Socket, RecordingSocket(events, **(socket_kwargs or {})))
    self.set_deck(NimbusDeck())
    self.set_heads({})

  async def _phase(self, name: str) -> None:
    self.events.append(name)
    if name == self.fail_at:
      raise RuntimeError(f"failed at {name}")
    if name == self.block_at:
      if self.phase_started is not None:
        self.phase_started.set()
      await anyio.sleep_forever()

  async def _initialize_connection(self) -> None:
    await self._phase("protocol7")

  async def _register_client(self) -> None:
    await self._phase("protocol3")

  async def _discover_root(self) -> None:
    await self._phase("root")

  async def _wait_before_reconnect(self, delay: float) -> None:
    self.backoff_delays.append(delay)
    if self.recover_after_backoff:
      self.fail_at = None
    await anyio.lowlevel.checkpoint()

  @property
  def num_channels(self) -> int:
    return 8

  async def pick_up_tips(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def drop_tips(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def aspirate(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def dispense(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def pick_up_tips96(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def drop_tips96(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def aspirate96(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def dispense96(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def pick_up_resource(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def move_picked_up_resource(self, *args: Any, **kwargs: Any) -> None:
    pass

  async def drop_resource(self, *args: Any, **kwargs: Any) -> None:
    pass

  def can_pick_up_tip(self, *args: Any, **kwargs: Any) -> bool:
    return True


class ExampleCommand(HamiltonCommand):
  """Minimal command whose response interpretation is observable."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 42

  def interpret_response(self, response: CommandResponse) -> dict:
    del response
    return {"result": "ok"}


def command_response() -> bytes:
  """Build one complete literal-compatible HOI command response frame."""
  hoi = HoiPacket(
    interface_id=1,
    action_code=Hoi2Action.COMMAND_RESPONSE,
    action_id=42,
    params=b"",
  )
  harp = HarpPacket(
    src=Address(1, 1, 257),
    dst=Address(2, 1, 65535),
    seq=1,
    protocol=2,
    action_code=Hoi2Action.COMMAND_RESPONSE,
    payload=hoi.pack(),
  )
  return IpPacket(protocol=6, payload=harp.pack()).pack()


class TestHamiltonTCPLifecycle(AnyioTestBase):
  async def test_constructor_initializes_all_superclass_state_and_registers_instance(self) -> None:
    backend = RecordingHamiltonTCPBackend([])

    assert not backend.is_active
    assert backend.setup_finished is False
    assert backend._deck is not None
    assert backend._head == {}
    assert backend in MachineBackend.get_all_instances()
    assert backend._client_id is None
    assert backend.client_address is None

  async def test_native_entry_and_exit_order(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events)

    async with backend:
      assert backend.is_connected
      events.append("body")

    assert events == ["socket.enter", "protocol7", "protocol3", "root", "body", "socket.exit"]
    assert not backend.is_connected

  @pytest.mark.parametrize("phase", ["protocol7", "protocol3", "root"])
  async def test_each_handshake_failure_unwinds_socket(self, phase: str) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events, fail_at=phase)

    with pytest.raises(RuntimeError, match=f"failed at {phase}"):
      async with backend:
        pytest.fail("failed entry reached body")

    assert events[-1] == "socket.exit"
    assert not backend.is_connected
    assert not backend.is_active

  async def test_cancellation_during_handshake_unwinds_socket(self) -> None:
    events: list[str] = []
    started = anyio.Event()
    backend = RecordingHamiltonTCPBackend(events, block_at="protocol3", phase_started=started)

    async def enter() -> None:
      async with backend:
        pytest.fail("cancelled entry reached body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await started.wait()
      task_group.cancel_scope.cancel()

    assert events[-1] == "socket.exit"
    assert not backend.is_connected

  async def test_socket_close_failure_still_clears_connection_state(self) -> None:
    backend = RecordingHamiltonTCPBackend(
      [], socket_kwargs={"exit_error": RuntimeError("socket close failed")}
    )

    with pytest.raises(RuntimeError, match="socket close failed"):
      async with backend:
        pass
    assert not backend.is_connected

  async def test_sequential_reuse_repeats_complete_handshake(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events)

    async with backend:
      pass
    async with backend:
      pass

    cycle = ["socket.enter", "protocol7", "protocol3", "root", "socket.exit"]
    assert events == cycle + cycle

  async def test_command_after_exit_does_not_reconnect(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events)
    async with backend:
      pass

    with pytest.raises(RuntimeError, match="active lifespan"):
      await backend.write(b"command")
    assert events.count("socket.reconnect") == 0

  async def test_invalid_context_and_options_fail_before_socket_entry(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events)
    backend._deck = None
    with pytest.raises(RuntimeError, match="Deck not set"):
      async with backend:
        pass
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pass
    assert events == []

  async def test_reconnect_uses_socket_and_repeats_handshake(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events)

    async with backend:
      events.clear()
      backend._connected = False
      await backend._ensure_connected()

    assert events[:4] == ["socket.reconnect", "protocol7", "protocol3", "root"]
    assert "socket.enter" not in events

  async def test_reconnect_retries_with_exponential_backoff(
    self,
  ) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(
      events,
      socket_kwargs={"reconnect_effects": [OSError("one"), OSError("two"), None]},
    )

    async with backend:
      events.clear()
      backend._connected = False
      await backend._reconnect()

    assert backend.backoff_delays == [1.0, 2.0]
    assert events.count("socket.reconnect") == 3
    assert events.count("protocol7") == 1

  async def test_failed_reconnect_leaves_no_replacement_connection_after_exit(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(
      events,
      max_reconnect_attempts=2,
      socket_kwargs={"reconnect_effects": [OSError("one"), OSError("two")]},
    )
    socket = cast(RecordingSocket, backend.io)

    async with backend:
      backend._connected = False
      with pytest.raises(ConnectionError, match="Failed to reconnect after 2 attempts"):
        await backend._reconnect()

    assert not socket.connection_active
    assert not backend.is_connected

  async def test_failed_reconnect_handshake_is_unpublished_during_backoff(
    self,
  ) -> None:
    backend = RecordingHamiltonTCPBackend([], max_reconnect_attempts=2, recover_after_backoff=True)
    async with backend:
      backend.fail_at = "protocol7"
      backend._connected = False
      await backend._reconnect()

    assert not backend.is_connected

  @pytest.mark.parametrize("phase", ["protocol7", "protocol3", "root"])
  async def test_reconnect_cancellation_unpublishes_incomplete_handshake(self, phase: str) -> None:
    started = anyio.Event()
    backend = RecordingHamiltonTCPBackend([], phase_started=started)
    observed_cancellations: list[BaseException] = []

    async with backend:
      backend.block_at = phase
      backend._connected = False

      async def reconnect() -> None:
        try:
          await backend._reconnect()
        except anyio.get_cancelled_exc_class() as error:
          observed_cancellations.append(error)
          raise

      async with anyio.create_task_group() as task_group:
        task_group.start_soon(reconnect)
        await started.wait()
        task_group.cancel_scope.cancel()

      assert len(observed_cancellations) == 1
      assert not backend.is_connected
      backend.auto_reconnect = False
      writes = backend.events.count("write:2")
      with pytest.raises(ConnectionError, match="auto-reconnect disabled"):
        await backend.write(b"unsafe")
      assert backend.events.count("write:2") == writes

  @pytest.mark.parametrize("phase", ["protocol7", "protocol3", "root"])
  async def test_suppressed_inner_reconnect_cancellation_keeps_outer_lifespan_safe(
    self, phase: str
  ) -> None:
    backend = RecordingHamiltonTCPBackend([])

    async with backend:
      backend.block_at = phase
      backend._connected = False
      with anyio.move_on_after(0.01) as scope:
        await backend._reconnect()

      assert scope.cancel_called
      assert backend.is_active
      assert not backend.is_connected
      backend.auto_reconnect = False
      writes = len([event for event in backend.events if event.startswith("write:")])
      with pytest.raises(ConnectionError, match="auto-reconnect disabled"):
        await backend.write(b"unsafe")
      assert len([event for event in backend.events if event.startswith("write:")]) == writes

  async def test_send_command_uses_one_write_and_direct_framed_reads(self) -> None:
    frame = command_response()
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events, socket_kwargs={"response": frame})
    backend.client_address = Address(2, 1, 65535)

    async with backend:
      events.clear()
      result = await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)

    assert result == {"result": "ok"}
    assert events[:3] == ["write:1", "read:2", f"read:{len(frame) - 2}"]

  async def test_send_command_timeout_bounds_complete_transaction(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events, socket_kwargs={"block_read": True})
    backend.client_address = Address(2, 1, 65535)

    async with backend:
      with pytest.raises(TimeoutError):
        await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=0.01)

      assert not backend.is_connected
      backend.auto_reconnect = False
      with pytest.raises(ConnectionError, match="auto-reconnect disabled"):
        await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=0.01)

    assert events.count("write:1") == 1
    assert events.count("read:2") == 1

  async def test_external_cancellation_while_write_is_blocked_invalidates_connection(self) -> None:
    events: list[str] = []
    backend = RecordingHamiltonTCPBackend(events, socket_kwargs={"block_write": True})
    backend.client_address = Address(2, 1, 65535)
    socket = cast(RecordingSocket, backend.io)
    observed_cancellation: Optional[BaseException] = None

    async with backend:
      with anyio.CancelScope() as scope:

        async def cancel_write() -> None:
          await socket.write_started.wait()
          scope.cancel()

        async with anyio.create_task_group() as task_group:
          task_group.start_soon(cancel_write)
          try:
            await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)
          except anyio.get_cancelled_exc_class() as error:
            observed_cancellation = error
            raise

      assert observed_cancellation is socket.write_cancellation
      assert not backend.is_connected
      backend.auto_reconnect = False
      with pytest.raises(ConnectionError, match="auto-reconnect disabled"):
        await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)

    assert events.count("write:1") == 1
    assert events.count("read:2") == 0


class PublicSocketHamiltonBackend(RecordingHamiltonTCPBackend):
  """Use a real Socket while stubbing only the Hamilton handshake."""

  def __init__(self, host: str, port: int) -> None:
    super().__init__([])
    self.io = Socket(
      human_readable_device_name="Hamilton lifecycle test",
      host=host,
      port=port,
      read_timeout=1,
      write_timeout=1,
    )


@asynccontextmanager
async def serve_timeout_then_response(
  response: bytes,
) -> AsyncIterator[tuple[str, int, list[bytes]]]:
  """Serve a partial first frame and a complete frame after reconnect."""
  listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
  host, port = cast(tuple[str, int], listener.extra(SocketAttribute.local_address))
  requests: list[bytes] = []
  connections = 0

  async def handler(stream: SocketStream) -> None:
    nonlocal connections
    connections += 1
    connection = connections
    async with stream:
      requests.append(await stream.receive())
      if connection == 1:
        await stream.send(response[:5])
        await anyio.sleep_forever()
      await stream.send(response)

  async with listener, anyio.create_task_group() as task_group:
    task_group.start_soon(listener.serve, handler)
    try:
      yield host, port, requests
    finally:
      task_group.cancel_scope.cancel()


@asynccontextmanager
async def serve_header_then_response(
  response: bytes,
) -> AsyncIterator[tuple[str, int, list[bytes], anyio.Event]]:
  """Serve one frame header, then a complete frame after reconnect."""
  listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
  host, port = cast(tuple[str, int], listener.extra(SocketAttribute.local_address))
  requests: list[bytes] = []
  header_sent = anyio.Event()
  connections = 0

  async def handler(stream: SocketStream) -> None:
    nonlocal connections
    connections += 1
    connection = connections
    async with stream:
      requests.append(await stream.receive())
      if connection == 1:
        await stream.send(response[:2])
        header_sent.set()
        await anyio.sleep_forever()
      await stream.send(response)

  async with listener, anyio.create_task_group() as task_group:
    task_group.start_soon(listener.serve, handler)
    try:
      yield host, port, requests, header_sent
    finally:
      task_group.cancel_scope.cancel()


class TestHamiltonTCPPublicSocketRecovery(AnyioTestBase):
  async def test_explicit_reconnect_discards_partial_frame_and_restores_requests(self) -> None:
    frame = command_response()
    async with serve_timeout_then_response(frame) as (host, port, requests):
      backend = PublicSocketHamiltonBackend(host, port)
      backend.client_address = Address(2, 1, 65535)

      async with backend:
        with pytest.raises(TimeoutError):
          await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=0.01)
        assert not backend.is_connected

        await backend._reconnect()
        result = await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)

      assert result == {"result": "ok"}
      assert len(requests) == 2

  async def test_external_cancellation_after_header_requires_reconnect(self) -> None:
    frame = command_response()
    async with serve_header_then_response(frame) as (host, port, requests, header_sent):
      backend = PublicSocketHamiltonBackend(host, port)
      backend.client_address = Address(2, 1, 65535)

      async with backend:
        with anyio.CancelScope() as scope:

          async def cancel_after_header() -> None:
            await header_sent.wait()
            scope.cancel()

          async with anyio.create_task_group() as task_group:
            task_group.start_soon(cancel_after_header)
            await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)

        assert scope.cancel_called
        assert not backend.is_connected
        backend.auto_reconnect = False
        with pytest.raises(ConnectionError, match="auto-reconnect disabled"):
          await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)
        assert len(requests) == 1

        backend.auto_reconnect = True
        await backend._reconnect()
        result = await backend.send_command(ExampleCommand(Address(1, 1, 257)), timeout=1)

      assert result == {"result": "ok"}
      assert len(requests) == 2
