"""Hamilton TCP communication layer.

HamiltonTCPClient
-----------------
Standalone, instrument-agnostic TCP transport for Hamilton HOI/HARP protocol.
Use directly in notebooks/scripts for discovery, introspection, and firmware
interaction without a LiquidHandler. Also composed by instrument backends as
``self.client``.

Usage (standalone)::

    client = HamiltonTCPClient(host="192.168.100.102")
    await client.setup()
    intro = HamiltonIntrospection(client)
    registry = await intro.build_type_registry("MLPrepRoot.MphRoot.MPH")

Usage (in backends)::

    self.client = HamiltonTCPClient(host=host, port=port)
    await self.client.setup()
    await self.client.send_command(SomeCommand(...))

Backends may construct the client with host/port (using this module's defaults)
or accept a pre-built client from the caller (dependency injection) so TCP
options stay in one place.

Error handling: By default (detailed_errors=True), command failures include
Layer A (HC_RESULT enum description) and Layer B (async method signature /
parameter diagnosis). Set detailed_errors=False to skip Layer B.

Key classes
-----------
- ObjectRegistry: maps dot-path strings to Address (e.g. "MLPrepRoot.MphRoot.MPH")
- RegistryProxy: dot-syntax accessor (client.interfaces.MLPrepRoot.MphRoot.MPH.address)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union, cast

from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket
from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
  GET_SUBOBJECT_ADDRESS,
  GlobalTypePool,
  HamiltonIntrospection,
  ObjectInfo,
  TypeRegistry,
  describe_hc_result,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
  parse_hamilton_error_params,
  RegistrationMessage,
  RegistrationResponse,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import (
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)

logger = logging.getLogger(__name__)


class ObjectRegistry:
  """Maps object paths to addresses. Depth-1 eager by default; lazy resolution beyond."""

  def __init__(self, transport: "HamiltonTCPClient"):
    self._transport = transport
    self._objects: Dict[str, ObjectInfo] = {}
    self._root_addresses: List[Address] = []

  def set_root_addresses(self, addresses: List[Address]) -> None:
    self._root_addresses = list(addresses)

  def get_root_addresses(self) -> List[Address]:
    return list(self._root_addresses)

  def register(self, path: str, obj: ObjectInfo) -> None:
    self._objects[path] = obj

  def has(self, path: str) -> bool:
    return path in self._objects

  def find_path_ending_with(self, suffix: str) -> Optional[str]:
    """Return a registered path whose last component equals suffix (e.g. 'Pipette' or 'DoorLock')."""
    for path in self._objects:
      if path == suffix or path.endswith("." + suffix):
        return path
    return None

  def address(self, path: str) -> Address:
    obj = self._objects.get(path)
    if obj is None:
      raise KeyError(f"Object '{path}' not discovered")
    return obj.address

  def path(self, address: Address) -> Optional[str]:
    """Return the registered object path for this address, or None if not in registry."""
    return self.find_path_by_address(address)

  def find_path_by_address(self, address: Address) -> Optional[str]:
    """Return the registered object path for this address, or None if not in registry."""
    for path, obj in self._objects.items():
      if (
        obj.address.module == address.module
        and obj.address.node == address.node
        and obj.address.object == address.object
      ):
        return path
    return None

  async def resolve(self, path: str) -> Address:
    """Resolve a dot-path to an Address, lazy-resolving and registering as needed.

    Uses the object's method table (GetMethod) to determine which Interface 0
    methods are supported; only calls GetSubobjectAddress when the parent
    supports it. Interfaces are per-object (no aggregation from children).
    """
    if path in self._objects:
      return self._objects[path].address
    parts = [p for p in path.split(".") if p]
    if not parts:
      raise KeyError(f"Invalid path: '{path}'")
    parent_path = ".".join(parts[:-1])
    child_name = parts[-1]
    introspection = HamiltonIntrospection(self._transport)

    if not parent_path:
      if not self._root_addresses:
        raise KeyError("No root addresses; run discovery first")
      parent_addr = self._root_addresses[0]
      parent_info = await introspection.get_object(parent_addr)
      parent_info.children = {}
      self.register(parent_info.name, parent_info)
      if parent_info.name == child_name:
        return parent_info.address
      raise KeyError(f"Root object is '{parent_info.name}', not '{child_name}'")

    parent_addr = await self.resolve(parent_path)
    parent_info = self._objects[parent_path]
    supported = await introspection.get_supported_interface0_method_ids(parent_info.address)
    if GET_SUBOBJECT_ADDRESS not in supported:
      raise KeyError(
        f"Object at path '{parent_path}' does not support GetSubobjectAddress "
        f"(interface 0, method 3); cannot resolve child '{child_name}'"
      )
    for i in range(parent_info.subobject_count):
      sub_addr = await introspection.get_subobject_address(parent_info.address, i)
      sub_info = await introspection.get_object(sub_addr)
      sub_info.children = {}
      child_path = f"{parent_path}.{sub_info.name}"
      parent_info.children[sub_info.name] = sub_info
      self.register(child_path, sub_info)
      if sub_info.name == child_name:
        return sub_info.address
    raise KeyError(f"Child '{child_name}' not found under '{parent_path}'")


class RegistryProxy:
  """Chainable dot-syntax accessor over ObjectRegistry.

  Routing: .address for required paths (KeyError if missing). Optional paths: .is_available
  or a firmware probe, per interface. Depth-2+ paths: await .resolve() once before .address.
  .info for metadata; __dir__ for tab-completion.
  """

  def __init__(self, registry: "ObjectRegistry", path: str = ""):
    object.__setattr__(self, "_registry", registry)
    object.__setattr__(self, "_path", path)

  def __getattr__(self, name: str) -> "RegistryProxy":
    current = object.__getattribute__(self, "_path")
    new_path = name if not current else f"{current}.{name}"
    return RegistryProxy(object.__getattribute__(self, "_registry"), new_path)

  def __getitem__(self, name: str) -> "RegistryProxy":
    """Support backend.interfaces['RootName'] or backend.interfaces['Root.Child']."""
    current = object.__getattribute__(self, "_path")
    if not current:
      new_path = name
    elif "." in name:
      new_path = name
    else:
      new_path = f"{current}.{name}"
    return RegistryProxy(object.__getattribute__(self, "_registry"), new_path)

  def __dir__(self):
    current = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    prefix = f"{current}." if current else ""
    children = set()
    for key in registry._objects:
      if key.startswith(prefix):
        segment = key[len(prefix) :].split(".")[0]
        if segment:
          children.add(segment)
    return list(children)

  async def resolve(self) -> "RegistryProxy":
    """Lazily resolve this path (depth-2+). Must be awaited once before .address is accessible."""
    path = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    await registry.resolve(path)
    return self

  @property
  def address(self) -> Address:
    """Wire-level destination for this path. Use for required interfaces; KeyError if not discovered."""
    path = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    return cast(Address, registry.address(path))

  @property
  def info(self) -> ObjectInfo:
    path = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    obj = registry._objects.get(path)
    if obj is None:
      raise KeyError(f"'{path}' not in registry. Call await .resolve() first.")
    return cast(ObjectInfo, obj)

  @property
  def is_available(self) -> bool:
    """True if this path was discovered and is present in the registry."""
    path = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    return path in registry._objects

  def __repr__(self) -> str:
    path = object.__getattribute__(self, "_path")
    registry = object.__getattribute__(self, "_registry")
    registered = path in registry._objects
    return f"<RegistryProxy '{path}' {'registered' if registered else 'unresolved'}>"


@dataclass
class InterfaceSpec:
  """Spec for a backend interface: instrument path, required flag, and raise-when-missing behavior.

  Logs use the dict key (name) and path only; no display_name.
  """

  path: str
  required: bool
  raise_when_missing: bool = True


class HamiltonInterfaceResolver:
  """Resolves named interfaces (path -> Address) with caching and required/optional behavior.

  Used by Nimbus and Prep backends. Holds client, interfaces dict, and _resolved cache.
  """

  def __init__(self, client: "HamiltonTCPClient", interfaces: dict[str, InterfaceSpec]):
    self.client = client
    self.interfaces = interfaces
    self._resolved: dict[str, Optional[Address]] = {}

  def clear(self) -> None:
    """Clear cached addresses (for reconnect-safe setup)."""
    self._resolved.clear()

  def has_interface(self, name: str) -> bool:
    """Return True if the interface was resolved and is present."""
    return name in self._resolved and self._resolved[name] is not None

  async def get(self, name: str) -> Optional[Address]:
    """Resolve once and cache. Required + missing -> raise. Optional + missing -> cache None, return None."""
    if name not in self.interfaces:
      raise KeyError(f"Unknown interface: {name}")
    spec = self.interfaces[name]
    if name in self._resolved:
      return self._resolved[name]
    try:
      await self.client.interfaces[spec.path].resolve()
      addr = self.client.interfaces[spec.path].address
      self._resolved[name] = addr
      logger.debug("Resolved %s → %s (%s)", name, addr, spec.path)
      return addr
    except KeyError:
      if spec.required:
        msg = f"Could not find interface '{name}' ({spec.path}) on instrument."
        raise RuntimeError(msg) from None
      self._resolved[name] = None
      return None

  async def require(self, name: str) -> Address:
    """Return address or raise. If optional and missing: log warning when raise_when_missing, then raise."""
    if name not in self.interfaces:
      raise KeyError(f"Unknown interface: {name}")
    spec = self.interfaces[name]
    msg = f"Could not find interface '{name}' ({spec.path}) on instrument."
    if name in self._resolved:
      if self._resolved[name] is None:
        if spec.raise_when_missing:
          logger.warning("%s", msg)
        raise RuntimeError(msg) from None
      addr = self._resolved[name]
      assert addr is not None
      return addr
    try:
      await self.client.interfaces[spec.path].resolve()
      addr = cast(Address, self.client.interfaces[spec.path].address)
      self._resolved[name] = addr
      logger.debug("Resolved %s → %s (%s)", name, addr, spec.path)
      return addr
    except KeyError:
      if spec.required:
        raise RuntimeError(msg) from None
      self._resolved[name] = None
      if spec.raise_when_missing:
        logger.warning("%s", msg)
      raise RuntimeError(msg) from None

  async def run_setup_loop(self) -> None:
    """Clear cache, then resolve all interfaces: required fail-fast; optional log and continue."""
    self.clear()
    for name, spec in self.interfaces.items():
      if spec.required:
        addr = await self.require(name)
        logger.debug("Found interface '%s' (%s) at %s", name, spec.path, addr)
      else:
        optional_addr = await self.get(name)
        if optional_addr is not None:
          logger.debug("Found interface '%s' (%s) at %s", name, spec.path, optional_addr)
        else:
          logger.debug("Could not find interface '%s' (%s) on instrument.", name, spec.path)

    found = sorted(name for name in self.interfaces if self.has_interface(name))
    optional_missing = sorted(
      name
      for name, spec in self.interfaces.items()
      if not spec.required and not self.has_interface(name)
    )
    logger.info("Interfaces: %s", ", ".join(found))
    if optional_missing:
      logger.info("Optional not present: %s", ", ".join(optional_missing))


@dataclass
class HamiltonError:
  """Hamilton error response."""

  error_code: int
  error_message: str
  interface_id: int
  action_id: int


class ErrorParser:
  """Parse Hamilton error responses."""

  @staticmethod
  def parse_error(data: bytes) -> HamiltonError:
    """Parse error response from Hamilton instrument."""
    # Error responses have a specific format
    # This is a simplified implementation - real errors may vary
    if len(data) < 8:
      raise ValueError("Error response too short")

    # Parse error structure (simplified)
    error_code = Reader(data).u32()
    error_message = data[4:].decode("utf-8", errors="replace")

    return HamiltonError(
      error_code=error_code, error_message=error_message, interface_id=0, action_id=0
    )


class HamiltonTCPClient:
  """Hamilton TCP communication and introspection (instrument-agnostic).

  Handles connection, Protocol 7/3, discovery, object registry, and command
  execution. Use standalone for discovery notebooks or assign to
  self.client in PrepBackend/NimbusBackend. Does not implement liquid-handling.
  interfaces (RegistryProxy): .address for required paths; .is_available or
  firmware probe for optional; await .resolve() for depth-2+ paths.
  detailed_errors=True (default) enables full diagnosis on command failure.
  Connection timeout is configurable; when the connection drops, the next
  send_command (with ensure_connection=True) reconnects and retries once.
  Backends use composition and optional dependency injection: they may build
  the client with host and port (using the defaults below) or accept an
  injected instance for full control.
  """

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
    detailed_errors: bool = True,
    connection_timeout: int = 300,
  ):
    """Initialize the Hamilton TCP client.

    These arguments are the defaults when backends construct the client with
    only host and port.

    Args:
      host: Instrument hostname or IP address.
      port: TCP port (default 2000).
      connection_timeout: Idle timeout in seconds sent to the instrument at
        connection init; if no commands are sent for this long the instrument
        may close the connection. Default 300 (5 min). If the connection drops,
        the next send_command (with ensure_connection=True) reconnects and
        retries that command once.
    """
    self.io = Socket(
      human_readable_device_name="Hamilton Liquid Handler",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self._connected = False
    self._reconnect_attempts = 0
    self.auto_reconnect = auto_reconnect
    self.max_reconnect_attempts = max_reconnect_attempts
    self.detailed_errors = detailed_errors
    self._connection_timeout = connection_timeout
    self._client_id: Optional[int] = None
    self.client_address: Optional[Address] = None
    self._sequence_numbers: Dict[Address, int] = {}
    self._registry = ObjectRegistry(self)
    self._type_registries: Dict[Address, TypeRegistry] = {}
    self._global_object_addresses: list[Address] = []

  @property
  def interfaces(self) -> RegistryProxy:
    """Dot-syntax access to the discovered object registry."""
    return RegistryProxy(self._registry)

  def discovered_root_name(self) -> str:
    """Return the root interface name (e.g. NimbusCORE, MLPrepRoot).

    Valid after setup(); use in backends to validate instrument type.
    """
    if not self._registry._objects:
      raise RuntimeError("No objects discovered. Call setup() first.")
    first_key = next(iter(self._registry._objects.keys()))
    return first_key.split(".")[0]

  async def _ensure_connected(self):
    """Ensure connection is healthy before operations."""
    if not self._connected:
      if not self.auto_reconnect:
        raise ConnectionError(
          f"{self.io._unique_id} Connection not established and auto-reconnect disabled"
        )
      logger.info(f"{self.io._unique_id} Connection not established, attempting to reconnect...")
      await self._reconnect()

  async def _reconnect(self):
    """Attempt to reconnect with exponential backoff."""
    if not self.auto_reconnect:
      raise ConnectionError(f"{self.io._unique_id} Auto-reconnect disabled")

    for attempt in range(self.max_reconnect_attempts):
      try:
        logger.info(
          f"{self.io._unique_id} Reconnection attempt {attempt + 1}/{self.max_reconnect_attempts}"
        )

        # Clean up existing connection
        try:
          await self.stop()
        except Exception:
          pass

        # Wait before reconnecting (exponential backoff)
        if attempt > 0:
          wait_time = 1.0 * (2 ** (attempt - 1))  # 1s, 2s, 4s, etc.
          await asyncio.sleep(wait_time)

        # Attempt to reconnect
        await self.setup()
        self._reconnect_attempts = 0
        logger.info(f"{self.io._unique_id} Reconnection successful")
        return

      except Exception as e:
        logger.warning(f"{self.io._unique_id} Reconnection attempt {attempt + 1} failed: {e}")

    # All reconnection attempts failed
    self._connected = False
    raise ConnectionError(
      f"{self.io._unique_id} Failed to reconnect after {self.max_reconnect_attempts} attempts"
    )

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Write data to the socket with connection state tracking.

    Args:
      data: The data to write.
      timeout: The timeout for writing to the server in seconds. If `None`, use the default timeout.
    """
    await self._ensure_connected()

    try:
      await self.io.write(data, timeout=timeout)
      self._connected = True
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    """Read data from the socket with connection state tracking.

    Args:
      num_bytes: Maximum number of bytes to read. Defaults to 128.
      timeout: The timeout for reading from the server in seconds. If `None`, use the default timeout.

    Returns:
      The data read from the socket.
    """
    await self._ensure_connected()

    try:
      data = await self.io.read(num_bytes, timeout=timeout)
      self._connected = True
      return data
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read exactly num_bytes with connection state tracking.

    Args:
      num_bytes: The exact number of bytes to read.
      timeout: The timeout for reading from the server in seconds. If `None`, use the default timeout.

    Returns:
      Exactly num_bytes of data.

    Raises:
      ConnectionError: If the connection is closed before num_bytes are read.
    """
    await self._ensure_connected()

    try:
      data = await self.io.read_exact(num_bytes, timeout=timeout)
      self._connected = True
      return data
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  @property
  def is_connected(self) -> bool:
    """Check if the connection is currently established."""
    return self._connected

  async def _read_one_message(self) -> Union[RegistrationResponse, CommandResponse]:
    """Read one complete Hamilton packet and parse based on protocol.

    Hamilton packets are length-prefixed:
    - First 2 bytes: packet size (little-endian)
    - Next packet_size bytes: packet payload

    The method inspects the IP protocol field and, for Protocol 6 (HARP),
    also checks the HARP protocol field to dispatch correctly.

    Returns:
      Union[RegistrationResponse, CommandResponse]: Parsed response

    Raises:
      ConnectionError: If connection is lost
      TimeoutError: If no message received within timeout
      ValueError: If protocol type is unknown
    """

    # Read packet size (2 bytes, little-endian)
    size_data = await self.read_exact(2)
    packet_size = Reader(size_data).u16()

    # Read packet payload
    payload_data = await self.read_exact(packet_size)
    complete_data = size_data + payload_data

    # Parse IP packet to get protocol field (byte 2)
    # Format: [size:2][ip_protocol:1][version:1][options_len:2][options:x][payload:n]
    ip_protocol = complete_data[2]

    # Dispatch based on IP protocol
    if ip_protocol == 6:
      # Protocol 6: HARP wrapper - need to check HARP protocol field
      # IP header: [size:2][protocol:1][version:1][options_len:2]
      ip_options_len = int.from_bytes(complete_data[4:6], "little")
      harp_start = 6 + ip_options_len

      # HARP header: [src:6][dst:6][seq:1][unk:1][harp_protocol:1][action:1]...
      # HARP protocol is at offset 14 within HARP packet
      harp_protocol_offset = harp_start + 14
      harp_protocol = complete_data[harp_protocol_offset]

      if harp_protocol == 2:
        # HARP Protocol 2: HOI2
        return CommandResponse.from_bytes(complete_data)
      if harp_protocol == 3:
        # HARP Protocol 3: Registration2
        return RegistrationResponse.from_bytes(complete_data)
      logger.warning(f"Unknown HARP protocol: {harp_protocol}, attempting CommandResponse parse")
      return CommandResponse.from_bytes(complete_data)

    logger.warning(f"Unknown IP protocol: {ip_protocol}, attempting CommandResponse parse")
    return CommandResponse.from_bytes(complete_data)

  async def setup(self):
    """Initialize Hamilton connection and discover objects.

    Hamilton uses strict request-response protocol:
    1. Establish TCP connection
    2. Protocol 7 initialization (get client ID)
    3. Protocol 3 registration
    4. Discover objects via Protocol 3 introspection
    """

    # Step 1: Establish TCP connection
    await self.io.setup()

    # Set connection state after successful connection
    self._connected = True
    self._reconnect_attempts = 0

    # Step 2: Initialize connection (Protocol 7)
    await self._initialize_connection()

    # Step 3: Register client (Protocol 3)
    await self._register_client()

    # Step 4: Discover root objects
    await self._discover_root()

    # Step 4b: Discover global objects (shared type definitions)
    await self._discover_globals()

    # Step 5: Register root object only (depth-1+ resolved lazily on demand)
    root_addresses = self._registry.get_root_addresses()
    if root_addresses:
      introspection = HamiltonIntrospection(self)
      root_info = await introspection.get_object(root_addresses[0])
      root_info.children = {}
      self._registry.register(root_info.name, root_info)

    root_name = self.discovered_root_name() if self._registry._objects else "—"
    logger.info(
      "Setup complete. Registered as Client ID %s (%s), Root: %s",
      self._client_id,
      self.client_address,
      root_name,
    )

  async def _initialize_connection(self):
    """Initialize connection using Protocol 7 (ConnectionPacket).

    Note: Protocol 7 doesn't have sequence numbers, so we send the packet
    and read the response directly (blocking) rather than using the
    normal routing mechanism.
    """
    logger.debug("Initializing Hamilton connection...")

    # Build Protocol 7 ConnectionPacket using new InitMessage
    packet = InitMessage(timeout=self._connection_timeout).build()

    logger.debug("[INIT] Sending Protocol 7 initialization packet:")
    logger.debug("[INIT]   Length: %s bytes", len(packet))
    logger.debug("[INIT]   Hex: %s", packet.hex(" "))

    # Send packet
    await self.write(packet)

    # Read response directly (blocking - safe because this is first communication)
    # Read packet size (2 bytes, little-endian)
    size_data = await self.read_exact(2)
    packet_size = Reader(size_data).u16()

    # Read packet payload
    payload_data = await self.read_exact(packet_size)
    response_bytes = size_data + payload_data

    logger.debug("[INIT] Received response:")
    logger.debug("[INIT]   Length: %s bytes", len(response_bytes))
    logger.debug("[INIT]   Hex: %s", response_bytes.hex(" "))

    # Parse response using InitResponse
    response = InitResponse.from_bytes(response_bytes)

    self._client_id = response.client_id
    # Controller module is 2, node is client_id, object 65535 for general addressing
    self.client_address = Address(2, response.client_id, 65535)

    logger.info(
      "Connection initialized (Client ID: %s, Address: %s)", self._client_id, self.client_address
    )

  async def _register_client(self):
    """Register client using Protocol 3."""
    logger.debug("Registering Hamilton client...")

    # Registration service address (DLL uses 0:0:65534, Piglet comment confirms)
    registration_service = Address(0, 0, 65534)

    # Step 1: Initial registration (action_code=0)
    reg_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.REGISTRATION_REQUEST
    )

    # Ensure client is initialized
    if self.client_address is None or self._client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    # Build and send registration packet
    seq = self._allocate_sequence_number(registration_service)
    packet = reg_msg.build(
      src=self.client_address,
      req_addr=Address(2, self._client_id, 65535),  # C# DLL: 2:{client_id}:65535
      res_addr=Address(0, 0, 0),  # C# DLL: 0:0:0
      seq=seq,
      harp_action_code=3,  # COMMAND_REQUEST
      harp_response_required=False,  # DLL uses 0x03 (no response flag)
    )

    logger.debug("[REGISTER] Sending registration packet:")
    logger.debug("[REGISTER]   Length: %s bytes, Seq: %s", len(packet), seq)
    logger.debug("[REGISTER]   Hex: %s", packet.hex(" "))
    logger.debug("[REGISTER]   Src: %s, Dst: %s", self.client_address, registration_service)

    # Send registration packet
    await self.write(packet)

    # Read response
    response = await self._read_one_message()

    logger.debug("[REGISTER] Received response:")
    logger.debug("[REGISTER]   Length: %s bytes", len(response.raw_bytes))
    logger.debug("[REGISTER]   Hex: %s", response.raw_bytes.hex(" "))

    logger.info("Client registered.")

  async def _discover_root(self):
    """Discover root objects via Protocol 3 HARP_PROTOCOL_REQUEST"""
    logger.debug("Discovering Hamilton root objects...")

    registration_service = Address(0, 0, 65534)

    # Request root objects (request_id=1)
    root_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST
    )
    root_msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST,
      protocol=2,
      request_id=HoiRequestId.ROOT_OBJECT_OBJECT_ID,
    )

    # Ensure client is initialized
    if self.client_address is None or self._client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = root_msg.build(
      src=self.client_address,
      req_addr=Address(0, 0, 0),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,  # COMMAND_REQUEST
      harp_response_required=True,  # Request with response
    )

    logger.debug("[DISCOVER_ROOT] Sending root object discovery:")
    logger.debug("[DISCOVER_ROOT]   Length: %s bytes, Seq: %s", len(packet), seq)
    logger.debug("[DISCOVER_ROOT]   Hex: %s", packet.hex(" "))

    # Send request
    await self.write(packet)

    # Read response
    response = await self._read_one_message()
    assert isinstance(response, RegistrationResponse)

    logger.debug("[DISCOVER_ROOT] Received response: %s bytes", len(response.raw_bytes))

    # Parse registration response to extract root object IDs
    root_objects = self._parse_registration_response(response)
    logger.debug("[DISCOVER_ROOT] Found %s root objects", len(root_objects))

    self._registry.set_root_addresses(root_objects)

    logger.debug("Discovery complete: %s root objects", len(root_objects))

  async def _discover_globals(self):
    """Discover global objects via Protocol 3 HARP_PROTOCOL_REQUEST.

    Global objects hold shared type definitions (structs/enums) referenced by
    source_id=1 in method parameter triples. Piglet calls these "globals" and
    uses request_id=2 (GLOBAL_OBJECT_ADDRESS) to discover them.
    """
    logger.debug("Discovering Hamilton global objects...")

    registration_service = Address(0, 0, 65534)

    global_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.HARP_PROTOCOL_REQUEST
    )
    global_msg.add_registration_option(
      RegistrationOptionType.HARP_PROTOCOL_REQUEST,
      protocol=2,
      request_id=HoiRequestId.GLOBAL_OBJECT_ADDRESS,
    )

    if self.client_address is None or self._client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = global_msg.build(
      src=self.client_address,
      req_addr=Address(0, 0, 0),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,  # COMMAND_REQUEST
      harp_response_required=True,
    )

    logger.debug("[DISCOVER_GLOBALS] Sending global object discovery:")
    logger.debug("[DISCOVER_GLOBALS]   Length: %s bytes, Seq: %s", len(packet), seq)
    logger.debug("[DISCOVER_GLOBALS]   Hex: %s", packet.hex(" "))

    await self.write(packet)

    response = await self._read_one_message()
    assert isinstance(response, RegistrationResponse)

    global_objects = self._parse_registration_response(response)
    self._global_object_addresses = global_objects
    logger.debug("[DISCOVER_GLOBALS] Found %s global objects", len(global_objects))

  def _parse_registration_response(self, response: RegistrationResponse) -> list[Address]:
    """Parse registration response options to extract object addresses.

    From Piglet: Option type 6 (HARP_PROTOCOL_RESPONSE) contains object IDs
    as a packed list of u16 values.

    Args:
      response: Parsed RegistrationResponse

    Returns:
      List of discovered object addresses
    """
    objects: list[Address] = []
    options_data = response.registration.options

    if not options_data:
      logger.debug("No options in registration response (no objects found)")
      return objects

    # Parse options: [option_id:1][length:1][data:x]
    reader = Reader(options_data)

    while reader.has_remaining():
      option_id = reader.u8()
      length = reader.u8()

      if option_id == RegistrationOptionType.HARP_PROTOCOL_RESPONSE:
        if length > 0:
          # Skip padding u16
          _ = reader.u16()

          # Read object IDs (u16 each)
          num_objects = (length - 2) // 2
          for _ in range(num_objects):
            object_id = reader.u16()
            # Objects are at Address(1, 1, object_id)
            objects.append(Address(1, 1, object_id))
      else:
        logger.warning(f"Unknown registration option ID: {option_id}, skipping {length} bytes")
        # Skip unknown option data
        reader.raw_bytes(length)

    return objects

  def _allocate_sequence_number(self, dest_address: Address) -> int:
    """Allocate next sequence number for destination.

    Args:
      dest_address: Destination object address

    Returns:
      Next sequence number for this destination
    """
    current = self._sequence_numbers.get(dest_address, 0)
    next_seq = (current + 1) % 256  # Wrap at 8 bits (1 byte)
    self._sequence_numbers[dest_address] = next_seq
    return next_seq

  async def send_command(
    self,
    command: HamiltonCommand,
    ensure_connection: bool = True,
    return_raw: bool = False,
    raise_on_error: bool = True,
  ) -> Any:
    """Send Hamilton command and wait for response.

    Sets source_address if not already set by caller (for testing).
    Uses backend's client_address assigned during Protocol 7 initialization.

    When ensure_connection=True (default), on connection error (broken pipe,
    reset, timeout, etc.) the backend reconnects and retries the command once.
    Pass ensure_connection=False for setup/discovery commands so they are sent
    once with no retry.

    Read/write timeouts are enforced at the backend level (read_timeout and
    write_timeout passed into HamiltonTCPClient and used by the Socket).

    Args:
      command: Hamilton command to execute.
      ensure_connection: If True, reconnect and retry once on connection error.
        If False, send once (for setup/discovery).
      return_raw: If True, return (params_bytes,) instead of parsing the
        response. Use with inspect_hoi_params() to debug wire format.
      raise_on_error: If True (default), log ERROR and raise on STATUS_EXCEPTION
        / COMMAND_EXCEPTION. If False, log DEBUG and return None (for probing
        many object/interface pairs without log spam).

    Returns:
      If return_raw=True: (params_bytes,). Otherwise parsed response
      (Command.Response instance, dict, or None). None if raise_on_error=False
      and the device returned an exception action.

    Raises:
      ConnectionError: If the connection is not established and auto_reconnect
        is disabled, or if reconnection fails.
      RuntimeError: If the Hamilton firmware returns an error action code and
        raise_on_error is True.
    """
    connection_errors = (
      BrokenPipeError,
      ConnectionError,
      ConnectionResetError,
      ConnectionAbortedError,
      TimeoutError,
      OSError,
    )
    max_attempts = 2 if ensure_connection else 1
    last_error: Optional[BaseException] = None

    for attempt in range(max_attempts):
      try:
        if command.source_address is None:
          if self.client_address is None:
            raise RuntimeError(
              "Backend not initialized - call setup() first to assign client_address"
            )
          command.source_address = self.client_address

        command.sequence_number = self._allocate_sequence_number(command.dest_address)
        message = command.build()

        log_params = command.get_log_params()
        logger.debug(f"{command.__class__.__name__} parameters: {log_params}")

        await self.write(message)
        response_message = await self._read_one_message()
        assert isinstance(response_message, CommandResponse)

        action = Hoi2Action(response_message.hoi.action_code)
        if action in (
          Hoi2Action.STATUS_EXCEPTION,
          Hoi2Action.COMMAND_EXCEPTION,
          Hoi2Action.INVALID_ACTION_RESPONSE,
        ):
          parsed = parse_hamilton_error_params(response_message.hoi.params)
          # Layer A: always append HC_RESULT enum description (synchronous, no round-trips)
          parsed_addr = HamiltonIntrospection.parse_error_address(parsed)
          hc_suffix = f" [{describe_hc_result(parsed_addr[3])}]" if parsed_addr else ""
          enriched_msg = f"Hamilton error {action.name} (action={action:#x}): {parsed}{hc_suffix}"
          if raise_on_error:
            logger.error(enriched_msg)
            if not self.detailed_errors:
              raise RuntimeError(enriched_msg)
            # Layer B: async TypeRegistry diagnosis (method signature, expected params)
            # Use the address from the error response so we resolve the method on the object that threw
            intro = HamiltonIntrospection(self)
            error_addr = parsed_addr[0] if parsed_addr else command.dest_address
            if error_addr not in self._type_registries:
              try:
                self._type_registries[error_addr] = await intro.build_type_registry(error_addr)
              except Exception:
                raise RuntimeError(enriched_msg)
            diagnostic = await intro.diagnose_error(enriched_msg, self._type_registries[error_addr])
            raise RuntimeError(diagnostic)
          logger.debug(enriched_msg)
          return None

        if return_raw:
          return (response_message.hoi.params,)
        return command.interpret_response(response_message)

      except connection_errors as e:
        last_error = e
        self._connected = False
        if not self.auto_reconnect or attempt == max_attempts - 1:
          raise
        logger.warning(
          f"{self.io._unique_id} Command failed (connection error), reconnecting and retrying: {e}"
        )
        await self._reconnect()

    assert last_error is not None
    raise last_error

  async def introspect(
    self, object_path: Optional[str] = None
  ) -> tuple[GlobalTypePool, TypeRegistry]:
    """Build introspection data on demand (for diagnostics/validation).

    Queries the device for global structs/enums and optionally builds a
    TypeRegistry for a specific object. Does not cache — each call queries
    the device fresh.

    Example::

      pool, reg = await client.introspect("MLPrepRoot.PipettorRoot.Pipettor")
      result = validate_struct(MyStruct, pool_struct, pool)
      sig = await intro.resolve_signature(addr, 1, 9, reg)

    Args:
      object_path: Optional dot-path to build a TypeRegistry for
        (e.g. "MLPrepRoot.PipettorRoot.Pipettor"). If None, returns
        an empty TypeRegistry with just the global pool attached.

    Returns:
      (GlobalTypePool, TypeRegistry) tuple.
    """
    intro = HamiltonIntrospection(self)
    pool = await intro.build_global_type_pool(self._global_object_addresses)
    if object_path:
      reg = await intro.build_type_registry(object_path, global_pool=pool)
    else:
      reg = TypeRegistry(address=None, global_pool=pool)
    return pool, reg

  async def stop(self):
    """Close connection."""
    try:
      await self.io.stop()
    except Exception as e:
      logger.warning(f"Error during stop: {e}")
    finally:
      self._connected = False
    logger.info("Hamilton TCP client stopped")

  def serialize(self) -> dict:
    """Serialize client configuration."""
    return {
      "client_id": self._client_id,
      "registry_paths": list(self._registry._objects.keys()),
    }
