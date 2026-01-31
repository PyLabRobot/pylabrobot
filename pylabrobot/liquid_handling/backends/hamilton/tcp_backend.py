"""Hamilton TCP Backend Base Class.

This module provides the base backend for all Hamilton TCP instruments.
It handles connection management, message routing, and the introspection API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Union

from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
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


class HamiltonTCPBackend(LiquidHandlerBackend):
  """Base backend for all Hamilton TCP instruments.

  Hamilton TCP instruments include the Nimbus and the Prep, using Hoi and Harp.
  STAR and Vantage use the other Hamilton protocol that works over USB.

  This class provides:
  - Connection management via Socket (wrapped with state tracking)
  - Protocol 7 initialization
  - Protocol 3 registration
  - Generic command execution
  - Object discovery via introspection

  Hamilton uses strict request-response protocol (no unsolicited messages),
  so we use simple direct read/write instead of complex routing.
  """

  def __init__(
    self,
    host: str,
    port: int,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    """Initialize Hamilton TCP backend.

    Args:
      host: Hamilton instrument IP address
      port: Hamilton instrument port (usually 50007)
      read_timeout: Read timeout in seconds
      write_timeout: Write timeout in seconds
      auto_reconnect: Enable automatic reconnection
      max_reconnect_attempts: Maximum reconnection attempts
    """

    self.io = Socket(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    # Connection state tracking (wrapping Socket)
    self._connected = False
    self._reconnect_attempts = 0
    self.auto_reconnect = auto_reconnect
    self.max_reconnect_attempts = max_reconnect_attempts

    # Hamilton-specific state
    self._client_id: Optional[int] = None
    self.client_address: Optional[Address] = None
    self._sequence_numbers: Dict[Address, int] = {}
    self._discovered_objects: Dict[str, list[Address]] = {}

    # Instrument-specific addresses (set by subclasses)
    self._instrument_addresses: Dict[str, Address] = {}

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

    logger.info(f"Hamilton backend setup complete. Client ID: {self._client_id}")

  async def _initialize_connection(self):
    """Initialize connection using Protocol 7 (ConnectionPacket).

    Note: Protocol 7 doesn't have sequence numbers, so we send the packet
    and read the response directly (blocking) rather than using the
    normal routing mechanism.
    """
    logger.info("Initializing Hamilton connection...")

    # Build Protocol 7 ConnectionPacket using new InitMessage
    packet = InitMessage(timeout=30).build()

    logger.info("[INIT] Sending Protocol 7 initialization packet:")
    logger.info(f"[INIT]   Length: {len(packet)} bytes")
    logger.info(f"[INIT]   Hex: {packet.hex(' ')}")

    # Send packet
    await self.write(packet)

    # Read response directly (blocking - safe because this is first communication)
    # Read packet size (2 bytes, little-endian)
    size_data = await self.read_exact(2)
    packet_size = Reader(size_data).u16()

    # Read packet payload
    payload_data = await self.read_exact(packet_size)
    response_bytes = size_data + payload_data

    logger.info("[INIT] Received response:")
    logger.info(f"[INIT]   Length: {len(response_bytes)} bytes")
    logger.info(f"[INIT]   Hex: {response_bytes.hex(' ')}")

    # Parse response using InitResponse
    response = InitResponse.from_bytes(response_bytes)

    self._client_id = response.client_id
    # Controller module is 2, node is client_id, object 65535 for general addressing
    self.client_address = Address(2, response.client_id, 65535)

    logger.info(f"[INIT] ✓ Client ID: {self._client_id}, Address: {self.client_address}")

  async def _register_client(self):
    """Register client using Protocol 3."""
    logger.info("Registering Hamilton client...")

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

    logger.info("[REGISTER] Sending registration packet:")
    logger.info(f"[REGISTER]   Length: {len(packet)} bytes, Seq: {seq}")
    logger.info(f"[REGISTER]   Hex: {packet.hex(' ')}")
    logger.info(f"[REGISTER]   Src: {self.client_address}, Dst: {registration_service}")

    # Send registration packet
    await self.write(packet)

    # Read response
    response = await self._read_one_message()

    logger.info("[REGISTER] Received response:")
    logger.info(f"[REGISTER]   Length: {len(response.raw_bytes)} bytes")
    logger.debug(f"[REGISTER]   Hex: {response.raw_bytes.hex(' ')}")

    logger.info("[REGISTER] ✓ Registration complete")

  async def _discover_root(self):
    """Discover root objects via Protocol 3 HARP_PROTOCOL_REQUEST"""
    logger.info("Discovering Hamilton root objects...")

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

    logger.info("[DISCOVER_ROOT] Sending root object discovery:")
    logger.info(f"[DISCOVER_ROOT]   Length: {len(packet)} bytes, Seq: {seq}")
    logger.info(f"[DISCOVER_ROOT]   Hex: {packet.hex(' ')}")

    # Send request
    await self.write(packet)

    # Read response
    response = await self._read_one_message()
    assert isinstance(response, RegistrationResponse)

    logger.debug(f"[DISCOVER_ROOT] Received response: {len(response.raw_bytes)} bytes")

    # Parse registration response to extract root object IDs
    root_objects = self._parse_registration_response(response)
    logger.info(f"[DISCOVER_ROOT] ✓ Found {len(root_objects)} root objects")

    # Store discovered root objects
    self._discovered_objects["root"] = root_objects

    logger.info(f"✓ Discovery complete: {len(root_objects)} root objects")

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

  async def send_command(self, command: HamiltonCommand, timeout: float = 10.0) -> Optional[dict]:
    """Send Hamilton command and wait for response.

    Sets source_address if not already set by caller (for testing).
    Uses backend's client_address assigned during Protocol 7 initialization.

    Args:
      command: Hamilton command to execute
      timeout: Maximum time to wait for response

    Returns:
      Parsed response dictionary, or None if command has no information to extract

    Raises:
      TimeoutError: If no response received within timeout
      HamiltonError: If command returned an error
    """
    # Set source address with smart fallback
    if command.source_address is None:
      if self.client_address is None:
        raise RuntimeError("Backend not initialized - call setup() first to assign client_address")
      command.source_address = self.client_address

    # Allocate sequence number for this command
    command.sequence_number = self._allocate_sequence_number(command.dest_address)

    # Build command message
    message = command.build()

    # Log command parameters for debugging
    log_params = command.get_log_params()
    logger.info(f"{command.__class__.__name__} parameters:")
    for key, value in log_params.items():
      # Format arrays nicely if very long
      if isinstance(value, list) and len(value) > 8:
        logger.info(f"  {key}: {value[:4]}... ({len(value)} items)")
      else:
        logger.info(f"  {key}: {value}")

    # Send command
    await self.write(message)

    # Read response (timeout handled by TCP layer)
    response_message = await self._read_one_message()
    assert isinstance(response_message, CommandResponse)

    # Check for error actions
    action = Hoi2Action(response_message.hoi.action_code)
    if action in (
      Hoi2Action.STATUS_EXCEPTION,
      Hoi2Action.COMMAND_EXCEPTION,
      Hoi2Action.INVALID_ACTION_RESPONSE,
    ):
      error_message = f"Error response (action={action:#x}): {response_message.hoi.params.hex()}"
      logger.error(f"Hamilton error {action}: {error_message}")
      raise RuntimeError(f"Hamilton error {action}: {error_message}")

    return command.interpret_response(response_message)

  async def stop(self):
    """Stop the backend and close connection."""
    try:
      await self.io.stop()
    except Exception as e:
      logger.warning(f"Error during stop: {e}")
    finally:
      self._connected = False
    logger.info("Hamilton backend stopped")

  def serialize(self) -> dict:
    """Serialize backend configuration."""
    return {
      **super().serialize(),
      "client_id": self._client_id,
      "instrument_addresses": {k: str(v) for k, v in self._instrument_addresses.items()},
    }
