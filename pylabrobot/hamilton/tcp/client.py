"""Hamilton TCP client for TCP-based instruments (Nimbus, Prep, etc.).

Use :attr:`HamiltonTCPClient.introspection` as the **only** supported entry for
Interface-0 discovery and type work (do not construct introspection classes
directly from application code).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple, Union, cast

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
from pylabrobot.device import Driver
from pylabrobot.hamilton.tcp.commands import TCPCommand, hamilton_error_for_entry
from pylabrobot.hamilton.tcp.error_tables import HC_RESULT_PROTOCOL
from pylabrobot.hamilton.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
  parse_hamilton_error_entries,
  parse_hamilton_error_params,
)
from pylabrobot.hamilton.tcp.introspection import (
  HamiltonIntrospection,
  MethodDescriptor,
  ObjectRegistry,
)
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import (
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)
from pylabrobot.hamilton.tcp.wire_types import HcResultEntry
from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket

logger = logging.getLogger(__name__)


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
    if len(data) < 8:
      raise ValueError("Error response too short")

    error_code = Reader(data).u32()
    error_message = data[4:].decode("utf-8", errors="replace")

    return HamiltonError(
      error_code=error_code, error_message=error_message, interface_id=0, action_id=0
    )


class _HcResultDescriptionHelper:
  """Resolves ``HcResultEntry`` to display strings and optional method context.

  Thin adapter over :attr:`HamiltonTCPClient.introspection` for Interface-0 metadata lookups
  used after static ``error_codes`` and :data:`HC_RESULT_PROTOCOL` tables.
  """

  def __init__(self, client: HamiltonTCPClient) -> None:
    self._client = client

  def clear(self) -> None:
    """No-op; introspection owns session caches."""
    return

  async def describe_entry(self, entry: HcResultEntry) -> Tuple[Optional[str], str]:
    addr = Address(entry.module_id, entry.node_id, entry.object_id)
    iface_name = await self._client.introspection.get_interface_name(addr, entry.interface_id)

    desc = self._client._error_codes.get(
      (entry.module_id, entry.node_id, entry.object_id, entry.action_id, entry.result)
    )
    if desc is None:
      desc = HC_RESULT_PROTOCOL.get(entry.result)
    if desc is None:
      desc = await self._client.introspection.get_hc_result_text(
        addr, entry.interface_id, entry.result
      )
    if desc is None:
      desc = f"HC_RESULT=0x{entry.result:04X}"
    return iface_name, desc

  async def format_entry_context(self, entry: HcResultEntry) -> Optional[str]:
    addr = Address(entry.module_id, entry.node_id, entry.object_id)
    path = self._client._registry.path(addr)
    path_part = f"path={path}" if path else "path=?"
    descriptor = await self._lookup_method_descriptor(addr, entry.interface_id, entry.action_id)
    if descriptor is None:
      return f"{path_part}, addr={addr}, iface={entry.interface_id}, action={entry.action_id}"
    return (
      f"{path_part}, addr={addr}, method={descriptor.id_string} {descriptor.signature_string()}"
    )

  async def _lookup_method_descriptor(
    self, addr: Address, interface_id: int, action_id: int
  ) -> Optional[MethodDescriptor]:
    try:
      method = await self._client.introspection.get_method_by_id(addr, interface_id, action_id)
      if method is None:
        return None
      return method.describe(None)
    except Exception as exc:
      logger.debug(
        "Method descriptor lookup failed for %s iface=%d action=%d: %s",
        addr,
        interface_id,
        action_id,
        exc,
      )
      return None


class HamiltonTCPClient(Driver):
  """Standalone transport + discovery/introspection client for Hamilton TCP devices."""

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
    connection_timeout: int = 600,
    error_codes: Optional[Dict[Tuple[int, int, int, int, int], str]] = None,
  ):
    super().__init__()

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
    self._connection_timeout = connection_timeout

    self._client_id: Optional[int] = None
    self.client_address: Optional[Address] = None
    self._sequence_numbers: Dict[Address, int] = {}
    self._discovered_objects: Dict[str, list[Address]] = {}
    self._instrument_addresses: Dict[str, Address] = {}
    self._registry = ObjectRegistry()
    self._global_object_addresses: list[Address] = []
    self._event_handlers: list[Callable[[CommandResponse], None]] = []
    self._error_codes: Dict[Tuple[int, int, int, int, int], str] = error_codes or {}
    self._introspection_impl: Optional[HamiltonIntrospection] = None
    self._hc_result_text = _HcResultDescriptionHelper(self)

  @property
  def registry(self) -> ObjectRegistry:
    """Object path registry for this session."""
    return self._registry

  @property
  def global_object_addresses(self) -> Sequence[Address]:
    """Global object addresses discovered during :meth:`setup` (read-only)."""
    return tuple(self._global_object_addresses)

  def get_root_object_addresses(self) -> list[Address]:
    """Roots from the registry, or from legacy ``_discovered_objects``."""
    roots = self._registry.get_root_addresses()
    if roots:
      return list(roots)
    return list(self._discovered_objects.get("root", []))

  @property
  def introspection(self) -> HamiltonIntrospection:
    """Lazy Interface-0 / type introspection facet (canonical entry)."""
    if self._introspection_impl is None:
      self._introspection_impl = HamiltonIntrospection(self)
    return self._introspection_impl

  def _invalidate_introspection_session(self) -> None:
    self._introspection_impl = None

  def on_event(self, callback: Callable[[CommandResponse], None]) -> Callable[[], None]:
    """Register a callback for ``Hoi2Action.EVENT`` frames.

    Returns an unsubscribe function. Callback exceptions are logged and swallowed.
    """
    self._event_handlers.append(callback)

    def _unsubscribe() -> None:
      try:
        self._event_handlers.remove(callback)
      except ValueError:
        pass

    return _unsubscribe

  def _dispatch_event(self, response_message: CommandResponse) -> None:
    for handler in list(self._event_handlers):
      try:
        handler(response_message)
      except Exception as exc:
        logger.exception("Event handler %r raised: %s", handler, exc)

  def _clear_session_state_for_setup(self) -> None:
    self._hc_result_text.clear()
    self._global_object_addresses = []
    self._invalidate_introspection_session()

  async def _ensure_connected(self):
    if not self._connected:
      if not self.auto_reconnect:
        raise ConnectionError(
          f"{self.io._unique_id} Connection not established and auto-reconnect disabled"
        )
      logger.info(f"{self.io._unique_id} Connection not established, attempting to reconnect...")
      await self._reconnect()

  async def _reconnect(self):
    if not self.auto_reconnect:
      raise ConnectionError(f"{self.io._unique_id} Auto-reconnect disabled")

    for attempt in range(self.max_reconnect_attempts):
      try:
        logger.info(
          f"{self.io._unique_id} Reconnection attempt {attempt + 1}/{self.max_reconnect_attempts}"
        )

        try:
          await self.stop()
        except Exception:
          pass

        if attempt > 0:
          wait_time = 1.0 * (2 ** (attempt - 1))
          await asyncio.sleep(wait_time)

        await self.setup()
        self._reconnect_attempts = 0
        logger.info(f"{self.io._unique_id} Reconnection successful")
        return

      except Exception as e:
        logger.warning(f"{self.io._unique_id} Reconnection attempt {attempt + 1} failed: {e}")

    self._connected = False
    raise ConnectionError(
      f"{self.io._unique_id} Failed to reconnect after {self.max_reconnect_attempts} attempts"
    )

  async def write(self, data: bytes, timeout: Optional[float] = None):
    await self._ensure_connected()

    try:
      await self.io.write(data, timeout=timeout)
      self._connected = True
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    await self._ensure_connected()

    try:
      data = await self.io.read(num_bytes, timeout=timeout)
      self._connected = True
      return cast(bytes, data)
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    await self._ensure_connected()

    try:
      data = await self.io.read_exact(num_bytes, timeout=timeout)
      self._connected = True
      return cast(bytes, data)
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  @property
  def is_connected(self) -> bool:
    return self._connected

  async def _read_one_message(
    self, timeout: Optional[float] = None
  ) -> Union[RegistrationResponse, CommandResponse]:
    size_data = await self.read_exact(2, timeout=timeout)
    packet_size = Reader(size_data).u16()

    payload_data = await self.read_exact(packet_size, timeout=timeout)
    complete_data = size_data + payload_data

    ip_protocol = complete_data[2]

    if ip_protocol == 6:
      ip_options_len = int.from_bytes(complete_data[4:6], "little")
      harp_start = 6 + ip_options_len
      harp_protocol_offset = harp_start + 14
      harp_protocol = complete_data[harp_protocol_offset]

      if harp_protocol == 2:
        resp = CommandResponse.from_bytes(complete_data)
        if resp.hoi.action_code == Hoi2Action.EVENT and self._event_handlers:
          self._dispatch_event(resp)
        return resp
      if harp_protocol == 3:
        return RegistrationResponse.from_bytes(complete_data)
      logger.warning(f"Unknown HARP protocol: {harp_protocol}, attempting CommandResponse parse")
      return CommandResponse.from_bytes(complete_data)

    logger.warning(f"Unknown IP protocol: {ip_protocol}, attempting CommandResponse parse")
    return CommandResponse.from_bytes(complete_data)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
    self._clear_session_state_for_setup()
    await self.io.setup()
    self._connected = True
    self._reconnect_attempts = 0
    await self._initialize_connection()
    await self._register_client()
    await self._discover_root()
    await self._discover_globals()

    root_addresses = self._registry.get_root_addresses()
    if root_addresses:
      root_info = await self.introspection.get_object(root_addresses[0])
      root_info.children = {}
      self._registry.register(root_info.name, root_info)

    logger.info(
      "Hamilton TCP client setup complete. Client ID: %s, globals: %d",
      self._client_id,
      len(self._global_object_addresses),
    )

  async def _initialize_connection(self):
    logger.info("Initializing Hamilton connection...")

    packet = InitMessage(timeout=self._connection_timeout).build()
    await self.write(packet)

    size_data = await self.read_exact(2)
    packet_size = Reader(size_data).u16()
    payload_data = await self.read_exact(packet_size)
    response_bytes = size_data + payload_data
    response = InitResponse.from_bytes(response_bytes)

    self._client_id = response.client_id
    self.client_address = Address(2, response.client_id, 65535)

  async def _register_client(self):
    logger.info("Registering Hamilton client...")
    registration_service = Address(0, 0, 65534)

    reg_msg = RegistrationMessage(
      dest=registration_service, action_code=RegistrationActionCode.REGISTRATION_REQUEST
    )

    if self.client_address is None or self._client_id is None:
      raise RuntimeError("Client not initialized - call _initialize_connection() first")

    seq = self._allocate_sequence_number(registration_service)
    packet = reg_msg.build(
      src=self.client_address,
      req_addr=Address(2, self._client_id, 65535),
      res_addr=Address(0, 0, 0),
      seq=seq,
      harp_action_code=3,
      harp_response_required=False,
    )

    await self.write(packet)
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

    if self.client_address is None or self._client_id is None:
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

    await self.write(packet)
    response = await self._read_one_message()
    assert isinstance(response, RegistrationResponse)

    root_objects = self._parse_registration_response(response)
    self._discovered_objects["root"] = root_objects
    self._registry.set_root_addresses(root_objects)

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

    if self.client_address is None or self._client_id is None:
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

    await self.write(packet)
    response = await self._read_one_message()
    assert isinstance(response, RegistrationResponse)
    self._global_object_addresses = self._parse_registration_response(response)

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
    current = self._sequence_numbers.get(dest_address, 0)
    next_seq = (current + 1) % 256
    self._sequence_numbers[dest_address] = next_seq
    return next_seq

  async def send_command(
    self,
    command: TCPCommand,
    ensure_connection: bool = True,
    return_raw: bool = False,
    raise_on_error: bool = True,
    read_timeout: Optional[float] = None,
  ) -> Any:
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
              "Client not initialized - call setup() first to assign client_address"
            )
          command.source_address = self.client_address

        command.sequence_number = self._allocate_sequence_number(command.dest_address)
        message = command.build()

        log_params = command.get_log_params()
        logger.debug(f"{command.__class__.__name__} parameters: {log_params}")

        await self.write(message)

        while True:
          response_message = await self._read_one_message(timeout=read_timeout)
          assert isinstance(response_message, CommandResponse)
          action = Hoi2Action(response_message.hoi.action_code)
          if action is Hoi2Action.COMMAND_ACK:
            logger.debug(
              "%s COMMAND_ACK from %s; awaiting terminal response",
              command.__class__.__name__,
              response_message.harp.src,
            )
            continue
          if action is Hoi2Action.EVENT:
            logger.debug(
              "%s EVENT from %s; skipping past to await terminal response",
              command.__class__.__name__,
              response_message.harp.src,
            )
            continue
          break

        if action in (
          Hoi2Action.STATUS_EXCEPTION,
          Hoi2Action.COMMAND_EXCEPTION,
          Hoi2Action.INVALID_ACTION_RESPONSE,
        ):
          entries = parse_hamilton_error_entries(response_message.hoi.params)
          if not entries:
            raw = parse_hamilton_error_params(response_message.hoi.params)
            enriched_msg = f"Hamilton error {action.name} (action={action:#x}): {raw}"
            if raise_on_error:
              logger.error(enriched_msg)
              raise RuntimeError(enriched_msg)
            logger.debug(enriched_msg)
            return None

          per_channel: Dict[int, Exception] = {}
          context_by_channel: Dict[int, Optional[str]] = {}
          for idx, entry in enumerate(entries):
            _iface_name, desc = await self._hc_result_text.describe_entry(entry)
            err = hamilton_error_for_entry(entry, desc)
            channel = command._channel_index_for_entry(idx, entry)
            if channel is None:
              channel = idx
            per_channel.setdefault(channel, err)
            if channel not in context_by_channel:
              context_by_channel[channel] = await self._hc_result_text.format_entry_context(entry)

          if raise_on_error:
            channel_summary = ", ".join(
              (
                f"ch{ch}: {per_channel[ch]} ({context_by_channel[ch]})"
                if context_by_channel.get(ch)
                else f"ch{ch}: {per_channel[ch]}"
              )
              for ch in sorted(per_channel)
            )
            logger.error(
              "Hamilton %s (action=%#x) on %d channel(s): %s",
              action.name,
              action,
              len(per_channel),
              channel_summary,
            )
            raise ChannelizedError(errors=per_channel, raw_response=response_message.hoi.params)
          logger.debug(
            "Hamilton %s (action=%#x) suppressed; entries=%d (raise_on_error=False)",
            action.name,
            action,
            len(entries),
          )
          return None

        if return_raw:
          return (response_message.hoi.params,)

        result = command.interpret_response(response_message)
        fatal = command.fatal_entries_by_channel(response_message)
        if fatal:
          fatal_per_channel: Dict[int, Exception] = {}
          fatal_context_by_channel: Dict[int, Optional[str]] = {}
          for ch, e in fatal.items():
            _iface_name, desc = await self._hc_result_text.describe_entry(e)
            fatal_per_channel[ch] = hamilton_error_for_entry(e, desc)
            fatal_context_by_channel[ch] = await self._hc_result_text.format_entry_context(e)
          logger.error(
            "Hamilton command fatal entries: %s",
            ", ".join(
              (
                f"ch{ch}: {fatal_per_channel[ch]} ({fatal_context_by_channel[ch]})"
                if fatal_context_by_channel.get(ch)
                else f"ch{ch}: {fatal_per_channel[ch]}"
              )
              for ch in sorted(fatal_per_channel)
            ),
          )
          raise ChannelizedError(errors=fatal_per_channel, raw_response=response_message.hoi.params)
        return result

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

  async def resolve_path(self, path: str) -> Address:
    """Resolve strict dot-path target to Address."""
    return await self._registry.resolve(path, self)

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

  async def get_firmware_tree(self, refresh: bool = False):
    """Return cached firmware tree, or build it through introspection."""
    return await self.introspection.get_firmware_tree(refresh=refresh)

  async def stop(self):
    try:
      await self.io.stop()
    except Exception as e:
      logger.warning(f"Error during stop: {e}")
    finally:
      self._connected = False
      self._invalidate_introspection_session()
    logger.info("Hamilton TCP client stopped")
