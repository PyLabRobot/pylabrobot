"""Hamilton TCP client for TCP-based instruments (Nimbus, Prep, etc.).

Use :attr:`HamiltonTCPClient.introspection` as the **only** supported entry for
Interface-0 discovery and type work.

Connection lifecycle
--------------------
Connecting is always caller-driven. :meth:`HamiltonTCPClient.setup` performs the
init handshake (during which the device assigns the client id that identifies the
session), registers the client, and discovers the root and global objects. There
is no automatic reconnection: if the connection drops, recovery is
``await client.stop()`` followed by ``await client.setup()``.

That is deliberate. Re-establishing the session cannot tell the caller whether an
in-flight command completed, and it does not stop instrument motion, so continuity
across a reconnect must never be assumed. Reconnecting silently would only hide
when that happened. :attr:`HamiltonTCPClient.is_connected` is available for callers
that want to implement their own policy.

Concurrency
-----------
One command is in flight at a time. Concurrent callers are serialized on a single
lock spanning write-through-terminal-response, so commands never interleave on the
socket. The instrument's own concurrency semantics over TCP are not yet
established; until they are, this is deliberately conservative and there is no
per-object or read/write parallelism (contrast ``pylabrobot.hamilton.star.lock``,
where the module topology is known well enough to overlap commands).

Responses must echo the request sequence and reverse its HARP addresses. A
timeout or cancellation without an observed terminal response leaves execution
uncertain and prevents further commands until stop() and setup(). A fresh
connection does not establish whether the instrument finished the old command.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import ClassVar, Dict, Optional, Sequence, Tuple, TypeVar, Union, cast

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.events import EventSubscription, OverflowPolicy
from pylabrobot.hamilton.transport.tcp.introspection import (
  HamiltonIntrospection,
  ObjectRegistry,
)
from pylabrobot.hamilton.transport.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
)
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import (
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)
from pylabrobot.hamilton.transport.tcp.session import SessionState, TCPSession
from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ConnectionInfo:
  """An immutable snapshot of connection identity and state, without socket access."""

  host: str
  port: int
  client_address: Optional[Address]
  state: SessionState


class HamiltonTCPClient:
  """Standalone transport + discovery/introspection client for Hamilton TCP devices."""

  _ERROR_CODES: ClassVar[Dict[Tuple[int, int, int, int, int], str]] = {}

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    connection_timeout: int = 600,
  ):
    self._host = host
    self._port = port
    self._write_timeout = write_timeout
    self._connection_timeout = connection_timeout
    self._read_timeout = read_timeout
    self._lifecycle_lock = asyncio.Lock()
    self._session = self._create_session()

  def _create_session(self) -> TCPSession:
    """Create an isolated owner for one connection's I/O and protocol state."""
    return TCPSession(
      Socket(
        human_readable_device_name="Hamilton Liquid Handler",
        host=self._host,
        port=self._port,
        read_timeout=self._read_timeout,
        write_timeout=self._write_timeout,
      ),
      read_timeout=self._read_timeout,
      error_codes=self._ERROR_CODES,
    )

  @property
  def connection_info(self) -> ConnectionInfo:
    """Return a snapshot that cannot bypass session I/O ownership."""
    return ConnectionInfo(self._host, self._port, self.client_address, self._session.state)

  @property
  def client_address(self) -> Optional[Address]:
    """Client address assigned to the current session by the instrument."""
    return self._session.client_address

  @property
  def registry(self) -> ObjectRegistry:
    """Object path registry for this session."""
    return self._session.registry

  @property
  def global_object_addresses(self) -> Sequence[Address]:
    """Global object addresses discovered during :meth:`setup` (read-only)."""
    return tuple(self._session.global_object_addresses)

  def get_root_object_addresses(self) -> list[Address]:
    """Root address from the registry as a single-element list."""
    addr = self._session.registry.get_root_address()
    return [addr] if addr is not None else []

  @property
  def introspection(self) -> HamiltonIntrospection:
    """Discovery and caches bound to the current connection's lifetime."""
    self._session.require_active()
    return self._session.introspection

  def events(self, capacity: int = 64, overflow: OverflowPolicy = "error") -> EventSubscription:
    """Subscribe to this session's events; overflow raises unless a drop policy is selected."""
    return self._session.events(capacity, overflow)

  def _require_connected(self) -> TCPSession:
    """Capture a usable session for a raw I/O operation."""
    session = self._session
    if session.state not in (SessionState.CONNECTING, SessionState.READY, SessionState.WAITING):
      raise ConnectionError(f"{session._io._unique_id} not connected - call stop() then setup()")
    return session

  async def _write_handshake(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Write handshake bytes once through the captured session."""
    session = self._require_connected()
    if session.reader_task is not None:
      raise RuntimeError("Use execute() for session transactions")
    try:
      await session._io.write(data, timeout=timeout)
    except asyncio.CancelledError:
      session.fail(ConnectionError("Hamilton TCP write cancelled"))
      raise
    except Exception as exc:
      session.fail(exc)
      raise

  async def _read_exact_handshake(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read a complete handshake field before the reader starts."""
    session = self._require_connected()
    if session.reader_task is not None:
      raise RuntimeError("The session reader owns the socket")
    try:
      return cast(bytes, await session._io.read_exact(num_bytes, timeout=timeout))
    except Exception as exc:
      session.fail(exc)
      raise

  @property
  def is_connected(self) -> bool:
    """Whether this session is usable, including an in-progress handshake."""
    return self._session.state in (
      SessionState.CONNECTING,
      SessionState.READY,
      SessionState.WAITING,
    )

  async def _read_one_message(
    self, timeout: Optional[float] = None
  ) -> Optional[Union[RegistrationResponse, CommandResponse]]:
    """Read a handshake frame before transferring ownership to the reader."""
    session = self._require_connected()
    if session.reader_task is not None:
      raise RuntimeError("The session reader owns the socket")
    return await session.read_message(timeout=timeout)

  async def setup(self) -> None:
    """Establish a fresh session, cleaning up any incomplete handshake on failure."""
    async with self._lifecycle_lock:
      if self._session.state is not SessionState.CLOSED:
        raise RuntimeError(
          f"{self._session._io._unique_id} already set up - call stop() before setting up again"
        )
      session = self._create_session()
      self._session = session
      session.state = SessionState.CONNECTING
      try:
        await session._io.setup()
        await self._initialize_connection()
        await self._register_client()
        await self._discover_root()
        await self._discover_globals()
        session.start_reader()
        root_addr = self._session.registry.get_root_address()
        if root_addr is not None:
          root_info = await self.introspection.get_object(root_addr)
          root_info.children = {}
          self._session.registry.register(root_info.name, root_info)
      except BaseException:
        await session.stop()
        raise
      logger.info(
        "Hamilton TCP client setup complete. Client ID: %s, globals: %d",
        self._session.client_id,
        len(self._session.global_object_addresses),
      )

  async def _initialize_connection(self):
    logger.info("Initializing Hamilton connection...")

    packet = InitMessage(timeout=self._connection_timeout).build()
    await self._write_handshake(packet)

    size_data = await self._read_exact_handshake(2)
    packet_size = Reader(size_data).u16()
    payload_data = await self._read_exact_handshake(packet_size)
    response_bytes = size_data + payload_data
    response = InitResponse.from_bytes(response_bytes)

    self._session.client_id = response.client_id
    self._session.client_address = Address(2, response.client_id, 65535)

  async def _register_client(self):
    logger.info("Registering Hamilton client...")
    registration_service = Address(0, 0, 65534)

    reg_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.REGISTRATION_REQUEST
    )

    if self.client_address is None or self._session.client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = reg_msg.build(
      src=self.client_address,
      req_addr=Address(2, self._session.client_id, 65535),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,
      harp_response_required=False,
    )

    await self._write_handshake(packet)
    await self._read_one_message()

  async def _discover_root(self):
    logger.info("Discovering Hamilton root objects...")

    registration_service = Address(0, 0, 65534)
    root_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST
    )
    root_msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST,
      protocol=2,
      request_id=HoiRequestId.ROOT_OBJECT_OBJECT_ID,
    )

    if self.client_address is None or self._session.client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = root_msg.build(
      src=self.client_address,
      req_addr=Address(0, 0, 0),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,
      harp_response_required=True,
    )

    await self._write_handshake(packet)
    response = await self._read_one_message()
    if not isinstance(response, RegistrationResponse):
      raise RuntimeError(
        f"Expected a RegistrationResponse during root discovery, got {type(response).__name__}"
      )

    root_objects = self._parse_registration_response(response)
    if len(root_objects) != 1:
      raise RuntimeError(
        f"Expected exactly one root object from discovery, got {len(root_objects)}: {root_objects}"
      )
    self._session.registry.set_root_address(root_objects[0])

  async def _discover_globals(self) -> None:
    logger.info("Discovering Hamilton global objects...")
    registration_service = Address(0, 0, 65534)
    global_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST
    )
    global_msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST,
      protocol=2,
      request_id=HoiRequestId.GLOBAL_OBJECT_ADDRESS,
    )

    if self.client_address is None or self._session.client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = global_msg.build(
      src=self.client_address,
      req_addr=Address(0, 0, 0),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,
      harp_response_required=True,
    )

    await self._write_handshake(packet)
    response = await self._read_one_message()
    if not isinstance(response, RegistrationResponse):
      raise RuntimeError(
        f"Expected a RegistrationResponse during global discovery, got {type(response).__name__}"
      )
    self._session.global_object_addresses.extend(self._parse_registration_response(response))

  def _parse_registration_response(self, response: RegistrationResponse) -> list[Address]:
    objects: list[Address] = []
    options_data = response.registration.options

    if not options_data:
      logger.debug("No options in registration response (no objects found)")
      return objects

    reader = Reader(options_data)
    while reader.has_remaining():
      option_id = reader.u8()
      length = reader.u8()

      if option_id == RegistrationOptionType.HARP_PROTOCOL_RESPONSE:
        if length > 0:
          _ = reader.u16()
          num_objects = (length - 2) // 2
          for _ in range(num_objects):
            object_id = reader.u16()
            objects.append(Address(1, 1, object_id))
      else:
        logger.warning(f"Unknown registration option ID: {option_id}, skipping {length} bytes")
        reader.raw_bytes(length)

    return objects

  def _allocate_sequence_number(self, dest_address: Address) -> int:
    """Allocate a sequence from the current session during the handshake."""
    return self._session.allocate_sequence(dest_address)

  async def execute(
    self,
    command: TCPCommand[ResultT],
    *,
    read_timeout: Optional[float] = None,
  ) -> ResultT:
    """Execute once and return the typed result, raising structured firmware errors.

    The request defines its operation's action. Errors preserve the wire payload
    and use offline descriptions; execution never issues diagnostic queries.
    """
    session = self._session
    return await session.execute(command, read_timeout=read_timeout)

  async def exchange(
    self,
    command: TCPCommand[object],
    *,
    read_timeout: Optional[float] = None,
  ) -> CommandResponse:
    """Return the full terminal response, including firmware exception frames.

    This is the raw protocol API. The caller owns decoding and firmware error
    handling. Transport failures always raise; requests are never retried.
    """
    session = self._session
    return await session.exchange(command, read_timeout=read_timeout)

  async def resolve_path(self, path: str) -> Address:
    """Resolve dot-path to Address (delegates to introspection)."""
    return await self.introspection.resolve_path(path)

  async def resolve_target(
    self,
    target: Union[Address, str],
    aliases: Optional[Dict[str, str]] = None,
  ) -> Address:
    """Resolve Address | alias | dot-path to Address."""
    if isinstance(target, Address):
      return target
    resolved = aliases.get(target, target) if aliases is not None else target
    return await self.resolve_path(resolved)

  async def stop(self) -> None:
    """Finish the current session before permitting setup to replace it."""
    async with self._lifecycle_lock:
      await self._session.stop()
      logger.info("Hamilton TCP client stopped")
