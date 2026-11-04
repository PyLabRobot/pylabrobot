"""Hamilton TCP client for TCP-based instruments (Nimbus, Prep, etc.).

Use :attr:`HamiltonTCPClient.introspection` as the supported entry point for
Interface-0 discovery and type work.

Connection lifecycle
--------------------
Entering :meth:`HamiltonTCPClient.lifespan` opens a fresh connection, performs
the init handshake (the instrument assigns the session's client ID), registers
the client, and discovers root and global objects. Exiting closes the session;
enter a new lifespan to recover from a dropped connection. Recovery is always
caller-driven. An in-flight command may have completed on the instrument even
if the connection failed, and reconnecting cannot establish its outcome or stop
instrument motion. :attr:`HamiltonTCPClient.is_connected` reports connection
state (including an in-progress handshake) for callers implementing their own
recovery policy.

Concurrency
-----------
Only one command is in flight at a time. Concurrent callers serialize on a
session lock spanning write through terminal response, so commands cannot
interleave on the socket. The instrument's TCP concurrency semantics are not
established; there is no per-object or read/write parallelism (unlike the known
module topology used by ``pylabrobot.hamilton.star.lock``).

Responses must echo the request sequence and reverse its HARP addresses. A
timeout or cancellation without an observed terminal response makes the session
uncertain and prevents further commands until a new lifespan is entered. A new
connection cannot establish whether the old command finished on the instrument.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar, Dict, Optional, Sequence, Tuple, TypeVar, Union, cast

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.events import EventSubscription, OverflowPolicy
from pylabrobot.hamilton.transport.tcp.introspection import HamiltonIntrospection, ObjectRegistry
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
  """Immutable snapshot of connection identity and state."""

  host: str
  port: int
  client_address: Optional[Address]
  state: SessionState


class HamiltonTCPClient(HasLifespan):
  """Standalone transport and discovery client for typed Hamilton TCP commands."""

  _ERROR_CODES: ClassVar[Dict[Tuple[int, int, int, int, int], str]] = {}

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    connection_timeout: int = 600,
  ):
    super().__init__()
    self._host = host
    self._port = port
    self._write_timeout = write_timeout
    self._connection_timeout = connection_timeout
    self._read_timeout = read_timeout
    self._session = self._create_session()

  def _create_session(self) -> TCPSession:
    """Create an isolated owner for one connection's protocol state."""
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
    """Return a snapshot without exposing socket ownership."""
    return ConnectionInfo(self._host, self._port, self.client_address, self._session.state)

  @property
  def client_address(self) -> Optional[Address]:
    """Client address assigned by the instrument to this session."""
    return self._session.client_address

  @property
  def registry(self) -> ObjectRegistry:
    """Object registry for this session."""
    return self._session.registry

  @property
  def global_object_addresses(self) -> Sequence[Address]:
    """Discovered global addresses for this session."""
    return tuple(self._session.global_object_addresses)

  def get_root_object_addresses(self) -> list[Address]:
    """Return the registered root address, if discovered."""
    addr = self._session.registry.get_root_address()
    return [addr] if addr is not None else []

  @property
  def introspection(self) -> HamiltonIntrospection:
    """Discovery and caches bound to the current connection."""
    self._session.require_active()
    return self._session.introspection

  def events(self, capacity: int = 64, overflow: OverflowPolicy = "error") -> EventSubscription:
    """Subscribe to events on the current session."""
    return self._session.events(capacity, overflow)

  def _require_connected(self) -> TCPSession:
    """Capture a usable session for handshake I/O."""
    session = self._session
    if session.state not in (SessionState.CONNECTING, SessionState.READY, SessionState.WAITING):
      raise ConnectionError(f"{session._io._unique_id} not connected - enter a new lifespan")
    return session

  async def _write_handshake(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Write handshake bytes before the session reader starts."""
    session = self._require_connected()
    if session.reader_started:
      raise RuntimeError("Use execute() for session transactions")
    try:
      await session._io.write(data, timeout=timeout)
    except BaseException:
      session.fail(ConnectionError("Hamilton TCP handshake write failed"))
      raise

  async def _read_exact_handshake(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read a complete handshake field before the reader starts."""
    session = self._require_connected()
    if session.reader_started:
      raise RuntimeError("The session reader owns the socket")
    try:
      return cast(bytes, await session._io.read_exact(num_bytes, timeout=timeout))
    except BaseException:
      session.fail(ConnectionError("Hamilton TCP handshake read failed"))
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
    """Read one frame before transferring ownership to the reader."""
    session = self._require_connected()
    if session.reader_started:
      raise RuntimeError("The session reader owns the socket")
    return await session.read_message(timeout=timeout)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open one fresh session and complete discovery before publishing it."""
    await super()._enter_lifespan(stack)
    session = self._create_session()
    self._session = session
    await stack.enter_async_context(session.lifespan())
    await self._initialize_connection()
    await self._register_client()
    await self._discover_root()
    await self._discover_globals()
    session.start_reader()
    root_addr = session.registry.get_root_address()
    if root_addr is not None:
      root_info = await self.introspection.get_object(root_addr)
      root_info.children = {}
      session.registry.register(root_info.name, root_info)
    logger.info(
      "Hamilton TCP client setup complete. Client ID: %s, globals: %d",
      session.client_id,
      len(session.global_object_addresses),
    )

  async def _initialize_connection(self) -> None:
    """Acquire the client ID using protocol 7."""
    await self._write_handshake(InitMessage(timeout=self._connection_timeout).build())
    size_data = await self._read_exact_handshake(2)
    payload_data = await self._read_exact_handshake(Reader(size_data).u16())
    response = InitResponse.from_bytes(size_data + payload_data)
    self._session.client_id = response.client_id
    self._session.client_address = Address(2, response.client_id, 65535)

  async def _register_client(self) -> None:
    """Register the client ID with the registration service."""
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

  async def _discover_root(self) -> None:
    """Discover and register the root object address."""
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
    """Discover global object addresses for the session."""
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
    """Decode the addresses in a registration response."""
    objects: list[Address] = []
    options_data = response.registration.options
    if not options_data:
      return objects
    reader = Reader(options_data)
    while reader.has_remaining():
      option_id = reader.u8()
      length = reader.u8()
      if option_id == RegistrationOptionType.HARP_PROTOCOL_RESPONSE:
        if length > 0:
          reader.u16()
          for _ in range((length - 2) // 2):
            objects.append(Address(1, 1, reader.u16()))
      else:
        logger.warning("Unknown registration option ID: %s, skipping %s bytes", option_id, length)
        reader.raw_bytes(length)
    return objects

  def _allocate_sequence_number(self, dest_address: Address) -> int:
    """Allocate a sequence number from the current session."""
    return self._session.allocate_sequence(dest_address)

  async def execute(
    self, command: TCPCommand[ResultT], *, read_timeout: Optional[float] = None
  ) -> ResultT:
    """Execute once and return the typed result."""
    return await self._session.execute(command, read_timeout=read_timeout)

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Return a full terminal response without retrying."""
    return await self._session.exchange(command, read_timeout=read_timeout)

  async def resolve_path(self, path: str) -> Address:
    """Resolve a dot-path through session introspection."""
    return await self.introspection.resolve_path(path)

  async def resolve_target(
    self, target: Union[Address, str], aliases: Optional[Dict[str, str]] = None
  ) -> Address:
    """Resolve an address, alias, or dot-path."""
    if isinstance(target, Address):
      return target
    return await self.resolve_path(aliases.get(target, target) if aliases is not None else target)
