"""NimbusDriver: TCP-based Driver for Hamilton Nimbus liquid handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, Optional, Union

from pylabrobot.device import Driver
from pylabrobot.io.binary import Reader
from pylabrobot.io.socket import Socket
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.introspection import (
  HamiltonIntrospection,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.messages import (
  CommandResponse,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.protocol import (
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)

from .commands import (
  GetChannelConfiguration_1,
  IsDoorLocked,
  LockDoor,
  Park,
  UnlockDoor,
)

if TYPE_CHECKING:
  from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

  from .pip_backend import NimbusPIPBackend

logger = logging.getLogger(__name__)


class NimbusDriver(Driver):
  """Driver for Hamilton Nimbus instruments over TCP.

  Owns the TCP connection, handles protocol initialization (Protocol 7 + Protocol 3),
  object discovery, and device-level operations (park, door lock).

  Capability-specific operations (pipetting) are handled by NimbusPIPBackend.
  """

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    super().__init__()

    self.io = Socket(
      human_readable_device_name="Hamilton Nimbus",
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    # Connection state
    self._connected = False
    self._reconnect_attempts = 0
    self.auto_reconnect = auto_reconnect
    self.max_reconnect_attempts = max_reconnect_attempts

    # Hamilton protocol state
    self._client_id: Optional[int] = None
    self.client_address: Optional[Address] = None
    self._sequence_numbers: Dict[Address, int] = {}
    self._discovered_objects: Dict[str, list[Address]] = {}

    # Instrument addresses (populated during setup)
    self._pipette_address: Optional[Address] = None
    self._door_lock_address: Optional[Address] = None
    self._nimbus_core_address: Optional[Address] = None
    self._num_channels: Optional[int] = None

    # Deck reference (set by Nimbus device before setup)
    self.deck: Optional[NimbusDeck] = None

    # PIP backend (created during setup)
    self.pip: NimbusPIPBackend  # set in setup()

  # ====================================================================
  # Connection management
  # ====================================================================

  async def _ensure_connected(self):
    """Ensure connection is healthy before operations."""
    if not self._connected:
      if not self.auto_reconnect:
        raise ConnectionError("Connection not established and auto-reconnect disabled")
      await self._reconnect()

  async def _reconnect(self):
    """Attempt to reconnect with exponential backoff."""
    import asyncio

    for attempt in range(self.max_reconnect_attempts):
      try:
        logger.info(f"Reconnection attempt {attempt + 1}/{self.max_reconnect_attempts}")
        try:
          await self.stop()
        except Exception:
          pass
        if attempt > 0:
          await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
        await self.setup()
        self._reconnect_attempts = 0
        logger.info("Reconnection successful")
        return
      except Exception as e:
        logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")

    self._connected = False
    raise ConnectionError(f"Failed to reconnect after {self.max_reconnect_attempts} attempts")

  async def write(self, data: bytes, timeout: Optional[float] = None):
    """Write data to the socket."""
    await self._ensure_connected()
    try:
      await self.io.write(data, timeout=timeout)
      self._connected = True
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read(self, num_bytes: int = 128, timeout: Optional[float] = None) -> bytes:
    """Read data from the socket."""
    await self._ensure_connected()
    try:
      data = await self.io.read(num_bytes, timeout=timeout)
      self._connected = True
      return data
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read exactly num_bytes from the socket."""
    await self._ensure_connected()
    try:
      data = await self.io.read_exact(num_bytes, timeout=timeout)
      self._connected = True
      return data
    except (ConnectionError, OSError, TimeoutError):
      self._connected = False
      raise

  async def _read_one_message(self) -> Union[RegistrationResponse, CommandResponse]:
    """Read one complete Hamilton packet and parse based on protocol."""
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

  # ====================================================================
  # Protocol initialization
  # ====================================================================

  async def _initialize_connection(self):
    """Initialize connection using Protocol 7 (ConnectionPacket)."""
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
    logger.info(f"Client ID: {self._client_id}, Address: {self.client_address}")

  async def _register_client(self):
    """Register client using Protocol 3."""
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
    logger.info("Registration complete")

  async def _discover_root(self):
    """Discover root objects via Protocol 3 HARP_PROTOCOL_REQUEST."""
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
    logger.info(f"Discovery complete: {len(root_objects)} root objects")

  def _parse_registration_response(self, response: RegistrationResponse) -> list[Address]:
    """Parse registration response options to extract object addresses."""
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
          _ = reader.u16()  # skip padding
          num_objects = (length - 2) // 2
          for _ in range(num_objects):
            object_id = reader.u16()
            objects.append(Address(1, 1, object_id))
      else:
        logger.warning(f"Unknown registration option ID: {option_id}, skipping {length} bytes")
        reader.raw_bytes(length)

    return objects

  def _allocate_sequence_number(self, dest_address: Address) -> int:
    """Allocate next sequence number for destination."""
    current = self._sequence_numbers.get(dest_address, 0)
    next_seq = (current + 1) % 256
    self._sequence_numbers[dest_address] = next_seq
    return next_seq

  # ====================================================================
  # Command dispatch
  # ====================================================================

  async def send_command(self, command: HamiltonCommand, timeout: float = 10.0) -> Optional[dict]:
    """Send Hamilton command and wait for response.

    Args:
      command: Hamilton command to execute
      timeout: Maximum time to wait for response

    Returns:
      Parsed response dictionary, or None if command has no information to extract

    Raises:
      RuntimeError: If backend not initialized or command returned an error
    """
    if command.source_address is None:
      if self.client_address is None:
        raise RuntimeError("Driver not initialized - call setup() first")
      command.source_address = self.client_address

    command.sequence_number = self._allocate_sequence_number(command.dest_address)
    message = command.build()

    log_params = command.get_log_params()
    logger.info(f"{command.__class__.__name__} parameters:")
    for key, value in log_params.items():
      if isinstance(value, list) and len(value) > 8:
        logger.info(f"  {key}: {value[:4]}... ({len(value)} items)")
      else:
        logger.info(f"  {key}: {value}")

    await self.write(message)

    response_message = await self._read_one_message()
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

    return command.interpret_response(response_message)

  # ====================================================================
  # Lifecycle
  # ====================================================================

  async def setup(self):
    """Initialize Hamilton connection, discover objects, and create PIP backend.

    1. Establish TCP connection
    2. Protocol 7 initialization (get client ID)
    3. Protocol 3 registration
    4. Discover root objects
    5. Discover instrument objects (Pipette, DoorLock, NimbusCore)
    6. Query channel configuration
    7. Create NimbusPIPBackend
    """
    # Open TCP connection
    await self.io.setup()
    self._connected = True
    self._reconnect_attempts = 0

    # Protocol initialization
    await self._initialize_connection()
    await self._register_client()
    await self._discover_root()

    # Discover instrument-specific objects
    await self._discover_instrument_objects()

    if self._pipette_address is None:
      raise RuntimeError("Pipette object not discovered. Cannot proceed with setup.")
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore root object not discovered. Cannot proceed with setup.")

    # Query channel configuration
    config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
    assert config is not None, "GetChannelConfiguration_1 command returned None"
    self._num_channels = config["channels"]
    logger.info(f"Channel configuration: {config['channels']} channels")

    # Create PIP backend
    from .pip_backend import NimbusPIPBackend

    self.pip = NimbusPIPBackend(self)

  async def _discover_instrument_objects(self):
    """Discover instrument-specific objects using introspection."""
    introspection = HamiltonIntrospection(self)

    root_objects = self._discovered_objects.get("root", [])
    if not root_objects:
      logger.warning("No root objects discovered")
      return

    nimbus_core_addr = root_objects[0]
    self._nimbus_core_address = nimbus_core_addr

    try:
      core_info = await introspection.get_object(nimbus_core_addr)

      for i in range(core_info.subobject_count):
        try:
          sub_addr = await introspection.get_subobject_address(nimbus_core_addr, i)
          sub_info = await introspection.get_object(sub_addr)

          if sub_info.name == "Pipette":
            self._pipette_address = sub_addr
            logger.info(f"Found Pipette at {sub_addr}")

          if sub_info.name == "DoorLock":
            self._door_lock_address = sub_addr
            logger.info(f"Found DoorLock at {sub_addr}")

        except Exception as e:
          logger.debug(f"Failed to get subobject {i}: {e}")

    except Exception as e:
      logger.warning(f"Failed to discover instrument objects: {e}")

    if self._door_lock_address is None:
      logger.info("DoorLock not available on this instrument")

  async def stop(self):
    """Stop the driver and close connection."""
    try:
      await self.io.stop()
    except Exception as e:
      logger.warning(f"Error during stop: {e}")
    finally:
      self._connected = False
    logger.info("Nimbus driver stopped")

  # ====================================================================
  # Properties
  # ====================================================================

  @property
  def num_channels(self) -> int:
    """The number of channels that the robot has."""
    if self._num_channels is None:
      raise RuntimeError("num_channels not set. Call setup() first.")
    return self._num_channels

  @property
  def is_connected(self) -> bool:
    """Check if the connection is currently established."""
    return self._connected

  # ====================================================================
  # Device-level operations
  # ====================================================================

  async def park(self):
    """Park the instrument."""
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore address not discovered. Call setup() first.")
    await self.send_command(Park(self._nimbus_core_address))
    logger.info("Instrument parked successfully")

  async def is_door_locked(self) -> bool:
    """Check if the door is locked.

    Raises:
      RuntimeError: If door lock is not available on this instrument.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )
    status = await self.send_command(IsDoorLocked(self._door_lock_address))
    assert status is not None, "IsDoorLocked command returned None"
    return bool(status["locked"])

  async def lock_door(self) -> None:
    """Lock the door.

    Raises:
      RuntimeError: If door lock is not available on this instrument.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )
    await self.send_command(LockDoor(self._door_lock_address))
    logger.info("Door locked successfully")

  async def unlock_door(self) -> None:
    """Unlock the door.

    Raises:
      RuntimeError: If door lock is not available on this instrument.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )
    await self.send_command(UnlockDoor(self._door_lock_address))
    logger.info("Door unlocked successfully")

  def serialize(self) -> dict:
    """Serialize driver configuration."""
    return {
      **super().serialize(),
      "host": self.io._host,
      "port": self.io._port,
      "client_id": self._client_id,
      "instrument_addresses": {
        k: str(v)
        for k, v in {
          "pipette": self._pipette_address,
          "door_lock": self._door_lock_address,
          "nimbus_core": self._nimbus_core_address,
        }.items()
        if v is not None
      },
    }
