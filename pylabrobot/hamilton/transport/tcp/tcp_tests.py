"""Handshake and lifespan regressions for the typed Hamilton TCP client."""

from typing import Any, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.io.socket import Socket
from pylabrobot.testing.concurrency import AnyioTestBase


class MemoryHandshakeSocket(Socket):
  """Serve fixed handshake frames without connecting to a device."""

  def __init__(self, events: list[str], responses: bytes = b"", exit_error: bool = False) -> None:
    super().__init__("handshake test", "127.0.0.1", 0)
    self.events = events
    self.responses = bytearray(responses)
    self.writes: list[bytes] = []
    self.exit_error = exit_error

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    assert not kwargs
    self.events.append("socket.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.events.append("socket.exit")
    if self.exit_error:
      raise RuntimeError("socket close failed")

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    assert self.is_active
    self.writes.append(data)
    self.events.append("write")
    await anyio.lowlevel.checkpoint()

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    assert self.is_active
    self.events.append(f"read:{num_bytes}")
    if len(self.responses) < num_bytes:
      await anyio.sleep_forever()
    data = bytes(self.responses[:num_bytes])
    del self.responses[:num_bytes]
    return data


class HandshakeClient(HamiltonTCPClient):
  """Replace only wire-facing handshake phases for deterministic lifecycle tests."""

  def __init__(
    self,
    events: list[str],
    *,
    fail_at: Optional[str] = None,
    block_at: Optional[str] = None,
    started: Optional[anyio.Event] = None,
    exit_error: bool = False,
  ) -> None:
    self.recorded_events = events
    self.sockets: list[MemoryHandshakeSocket] = []
    self.fail_at = fail_at
    self.block_at = block_at
    self.started = started
    self.exit_error = exit_error
    super().__init__("127.0.0.1", 0, connection_timeout=30)

  def _create_session(self) -> TCPSession:
    socket = MemoryHandshakeSocket(self.recorded_events, exit_error=self.exit_error)
    self.sockets.append(socket)
    return TCPSession(socket)

  async def _phase(self, name: str) -> None:
    self.recorded_events.append(name)
    if self.fail_at == name:
      raise RuntimeError(f"failed at {name}")
    if self.block_at == name:
      if self.started is not None:
        self.started.set()
      await anyio.sleep_forever()

  async def _initialize_connection(self) -> None:
    await self._phase("init")

  async def _register_client(self) -> None:
    await self._phase("register")

  async def _discover_root(self) -> None:
    await self._phase("root")

  async def _discover_globals(self) -> None:
    await self._phase("globals")


class LiteralInitClient(HandshakeClient):
  """Exercise the real protocol-7 handshake using a recorded response."""

  def _create_session(self) -> TCPSession:
    socket = MemoryHandshakeSocket(
      self.recorded_events,
      responses=bytes.fromhex(
        "1a000730000000000300011000000500021000000100041000001e00"
        "2e000630000000000000feff02000500ffff010003002a0000000000000000000000000000000000"
        "0000000000000000"
      ),
    )
    self.sockets.append(socket)
    return TCPSession(socket)

  async def _initialize_connection(self) -> None:
    await HamiltonTCPClient._initialize_connection(self)

  async def _register_client(self) -> None:
    await HamiltonTCPClient._register_client(self)


class TestTypedTCPHandshake(AnyioTestBase):
  async def test_literal_init_assigns_client_identity_before_registration(self) -> None:
    """A changed init frame or parser must not silently publish the wrong client ID."""
    events: list[str] = []
    client = LiteralInitClient(events)
    async with client:
      assert client.client_address == Address(2, 5, 65535)
      assert client._session.client_id == 5
      assert client._session.reader_started
    assert client.sockets[-1].writes == [
      bytes.fromhex("1a000730000000000300011000000000021000000100041000001e00"),
      bytes.fromhex(
        "2e000630000002000500ffff00000000feff010003032a000000000000000000000002000500ffff"
        "0000000000000000"
      ),
    ]
    assert events[:9] == [
      "socket.enter",
      "write",
      "read:2",
      "read:26",
      "write",
      "read:2",
      "read:46",
      "root",
      "globals",
    ]
    assert events[-1] == "socket.exit"

  async def test_handshake_finishes_before_reader_and_closes_on_exit(self) -> None:
    """Starting the reader during discovery would steal the handshake's socket reads."""
    events: list[str] = []
    client = HandshakeClient(events)
    async with client:
      assert client._session.reader_started
      assert client._session.state is SessionState.READY
      events.append("body")
    assert events == ["socket.enter", "init", "register", "root", "globals", "body", "socket.exit"]
    assert client._session.state is SessionState.CLOSED

  @pytest.mark.parametrize("phase", ["init", "register", "root", "globals"])
  async def test_failed_handshake_unwinds_owned_socket(self, phase: str) -> None:
    """Each discovery failure must release the newly acquired session."""
    events: list[str] = []
    client = HandshakeClient(events, fail_at=phase)
    with pytest.raises(RuntimeError, match=f"failed at {phase}"):
      async with client:
        pytest.fail("failed entry reached body")
    assert events[-1] == "socket.exit"
    assert client._session.state is SessionState.CLOSED
    assert not client.is_active

  async def test_cancelled_handshake_unwinds_owned_socket(self) -> None:
    """Cancellation during discovery cannot leave the socket open."""
    events: list[str] = []
    started = anyio.Event()
    client = HandshakeClient(events, block_at="globals", started=started)

    async def enter() -> None:
      async with client:
        pytest.fail("cancelled entry reached body")

    async with anyio.create_task_group() as tasks:
      tasks.start_soon(enter)
      await started.wait()
      tasks.cancel_scope.cancel()
    assert events[-1] == "socket.exit"
    assert client._session.state is SessionState.CLOSED

  async def test_new_lifespan_uses_new_session_and_repeats_discovery(self) -> None:
    """Reusing a client must not reuse the prior connection's identity or cache."""
    events: list[str] = []
    client = HandshakeClient(events)
    async with client:
      first = client._session
      first.client_id = 5
    async with client:
      assert client._session is not first
      assert client._session.client_id is None
      assert client._session.sequence_numbers == {}
    assert len(client.sockets) == 3  # unopened constructor session plus two owned sessions
    assert events == ["socket.enter", "init", "register", "root", "globals", "socket.exit"] * 2

  async def test_socket_close_failure_still_closes_session(self) -> None:
    """Socket cleanup errors must not leave a published usable session."""
    client = HandshakeClient([], exit_error=True)
    with pytest.raises(RuntimeError, match="socket close failed"):
      async with client:
        pass
    assert client._session.state is SessionState.CLOSED
    assert not client.is_connected

  async def test_invalid_options_fail_before_socket_entry(self) -> None:
    """Unsupported lifespan options must not acquire a connection."""
    events: list[str] = []
    client = HandshakeClient(events)
    with pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
      async with client.lifespan(unknown=True):
        pass
    assert events == []
