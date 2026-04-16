"""Hamilton TCP client for TCP-based instruments (Nimbus, Prep, etc.)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.hamilton.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
)
from pylabrobot.hamilton.tcp.introspection import HamiltonIntrospection, ObjectRegistry
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import (
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)
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


class HamiltonTCPClient(Driver):
  """Standalone transport + discovery/introspection client for Hamilton TCP devices."""

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
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

    self._client_id: Optional[int] = None
    self.client_address: Optional[Address] = None
    self._sequence_numbers: Dict[Address, int] = {}
    self._discovered_objects: Dict[str, list[Address]] = {}
    self._instrument_addresses: Dict[str, Address] = {}
    self._registry = ObjectRegistry()
    self._supported_interface0_method_ids: Dict[Address, Set[int]] = {}

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
      return data
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
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
    return self._connected

  async def _read_one_message(self) -> Union[RegistrationResponse, CommandResponse]:
    size_data = await self.read_exact(2)
    packet_size = Reader(size_data).u16()

    payload_data = await self.read_exact(packet_size)
    complete_data = size_data + payload_data

    ip_protocol = complete_data[2]

    if ip_protocol == 6:
      ip_options_len = int.from_bytes(complete_data[4:6], "little")
      harp_start = 6 + ip_options_len
      harp_protocol_offset = harp_start + 14
      harp_protocol = complete_data[harp_protocol_offset]

      if harp_protocol == 2:
        return CommandResponse.from_bytes(complete_data)
      if harp_protocol == 3:
        return RegistrationResponse.from_bytes(complete_data)
      logger.warning(f"Unknown HARP protocol: {harp_protocol}, attempting CommandResponse parse")
      return CommandResponse.from_bytes(complete_data)

    logger.warning(f"Unknown IP protocol: {ip_protocol}, attempting CommandResponse parse")
    return CommandResponse.from_bytes(complete_data)

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params  # reserved for capability-level startup params
    await self.io.setup()
    self._connected = True
    self._reconnect_attempts = 0
    await self._initialize_connection()
    await self._register_client()
    await self._discover_root()
    logger.info(f"Hamilton TCP client setup complete. Client ID: {self._client_id}")

  async def _initialize_connection(self):
    logger.info("Initializing Hamilton connection...")

    packet = InitMessage(timeout=30).build()
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
    command: HamiltonCommand,
    timeout: float = 10.0,
    ensure_connection: bool = True,
    return_raw: bool = False,
  ) -> Optional[Any]:
    del ensure_connection  # The client enforces connection checks internally.
    if command.source_address is None:
      if self.client_address is None:
        raise RuntimeError("Client not initialized - call setup() first to assign client_address")
      command.source_address = self.client_address

    command.sequence_number = self._allocate_sequence_number(command.dest_address)
    message = command.build()
    await self.write(message)

    if timeout is None:
      response_message = await self._read_one_message()
    else:
      response_message = await asyncio.wait_for(self._read_one_message(), timeout)
    assert isinstance(response_message, CommandResponse)

    action = Hoi2Action(response_message.hoi.action_code)
    if action in (
      Hoi2Action.STATUS_EXCEPTION,
      Hoi2Action.COMMAND_EXCEPTION,
      Hoi2Action.INVALID_ACTION_RESPONSE,
    ):
      error_message = f"Error response (action={action:#x}): {response_message.hoi.params.hex()}"
      logger.error(f"Hamilton error {action}: {error_message}")
      raise RuntimeError(f"Hamilton error {action}: {error_message}")

    if return_raw:
      return (response_message.hoi.params,)

    return command.interpret_response(response_message)

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

  async def get_supported_interface0_method_ids(self, address: Address) -> Set[int]:
    """Return cached supported Interface-0 methods for an address."""
    if address in self._supported_interface0_method_ids:
      return set(self._supported_interface0_method_ids[address])

    introspection = HamiltonIntrospection(self)
    obj = await introspection.get_object(address)
    supported: Set[int] = set()
    for i in range(obj.method_count):
      try:
        method = await introspection.get_method(address, i)
      except Exception as e:
        logger.debug("get_method(%s, %d) failed: %s", address, i, e)
        continue
      if method.interface_id == 0:
        supported.add(method.method_id)
    self._supported_interface0_method_ids[address] = supported
    return set(supported)

  async def get_firmware_tree(self, refresh: bool = False):
    """Return cached firmware tree, or build it through introspection."""
    return await HamiltonIntrospection(self).get_firmware_tree(refresh=refresh)

  async def print_firmware_tree(self, refresh: bool = False):
    """Print firmware tree text and return the tree object."""
    return await HamiltonIntrospection(self).print_firmware_tree(refresh=refresh)

  async def stop(self):
    try:
      await self.io.stop()
    except Exception as e:
      logger.warning(f"Error during stop: {e}")
    finally:
      self._connected = False
    logger.info("Hamilton TCP client stopped")

