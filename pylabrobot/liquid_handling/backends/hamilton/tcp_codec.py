"""
Hamilton TCP Communication Codec

This module provides a clean, three-layer packet structure for Hamilton protocol communication:
- IpPacket: Transport layer (size, protocol, version, options)
- Harp2: Protocol layer (addresses, sequence numbers, action fields)
- Hoi2: Application layer (command data, parameters)

The HamiltonMessage class combines all three layers into a complete message.
Command classes provide a clean API for building and parsing specific Hamilton commands.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Callable, Dict, Any

from pylabrobot.io.tcp import TCP

logger = logging.getLogger(__name__)


class HamiltonProtocol(IntEnum):
    """Hamilton protocol identifiers.

    These values are derived from the piglet Rust implementation:
    - Protocol 2: PIPETTE - pipette-specific operations (HamiltonMessage: IpPacket[HARP2[HOI2]])
    - Protocol 3: REGISTRATION - object registration and discovery (HamiltonMessage: IpPacket[HARP2[HOI2]])
    - Protocol 6: OBJECT_DISCOVERY - general object discovery and method calls (HamiltonMessage: IpPacket[HARP2[HOI2]])
    - Protocol 7: INITIALIZATION - connection initialization and client ID negotiation (IpMessage: IpPacket[Parameters])
    """
    PIPETTE = 0x02
    REGISTRATION = 0x03
    OBJECT_DISCOVERY = 0x06
    INITIALIZATION = 0x07




@dataclass
class ObjectAddress:
    """Hamilton object address (module_id, node_id, object_id)."""
    module_id: int
    node_id: int
    object_id: int

    def to_bytes(self) -> bytes:
        """Serialize to bytes (little-endian, like piglet)."""
        return struct.pack('<HHH', self.module_id, self.node_id, self.object_id)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'ObjectAddress':
        """Deserialize from bytes (little-endian, like piglet)."""
        if len(data) < 6:
            raise ValueError("Invalid ObjectAddress data - need 6 bytes")
        return cls(*struct.unpack('<HHH', data[:6]))

    def __str__(self) -> str:
        return f"{self.module_id}.{self.node_id}.{self.object_id}"


# ============================================================================
# THREE-LAYER PACKET STRUCTURE
# ============================================================================

@dataclass
class IpPacket:
    """Hamilton IpPacket2 - Transport layer packet (CORRECTED VERSION).

    Official Hamilton Specification:
    Bytes 00-01: size (2 bytes) - does NOT include size field itself
    Bytes 02:    protocol (1 byte)
    Bytes 03:    minor | major version (.5)|(.5) - split into two 4-bit fields
    Bytes 04-05: options length (2 bytes)
    Bytes 06+:   options (x bytes)
    Bytes:       payload
    """
    packet_size: int
    protocol: HamiltonProtocol
    version_major: int  # 4 bits
    version_minor: int  # 4 bits
    options: bytes
    payload: bytes  # Contains HARP2 packet

    def to_bytes(self) -> bytes:
        """Serialize IpPacket2 to bytes according to official Hamilton spec."""
        # Calculate total packet size: protocol(1) + version(1) + options_length(2) + options(x) + payload(y)
        # Size field does NOT include itself
        total_size = 1 + 1 + 2 + len(self.options) + len(self.payload)

        # Pack version as minor|major (4 bits each)
        version_byte = (self.version_minor & 0xF) | ((self.version_major & 0xF) << 4)

        return struct.pack('<HBBH',
                          total_size,
                          self.protocol,
                          version_byte,
                          len(self.options)) + self.options + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> 'IpPacket':
        """Deserialize IpPacket2 from bytes according to official Hamilton spec."""
        if len(data) < 6:  # H + B + B + H = 6 bytes minimum
            raise ValueError("IpPacket2 too short")

        packet_size, protocol, version_byte, options_length = struct.unpack('<HBBH', data[:6])

        # Extract minor/major version from packed byte
        version_minor = version_byte & 0xF
        version_major = (version_byte >> 4) & 0xF

        # Extract options and payload
        options = data[6:6+options_length]
        payload = data[6+options_length:]

        return cls(packet_size, HamiltonProtocol(protocol), version_major, version_minor, options, payload)

    @property
    def payload_offset(self) -> int:
        """Get the byte offset where payload starts."""
        return 6 + len(self.options)  # Header is 6 bytes + options length

    @property
    def payload_length(self) -> int:
        """Get the expected payload length."""
        return self.packet_size - 4 - len(self.options)  # Total - header - options


@dataclass
class Harp2:
    """Hamilton HARP2 - Protocol layer packet (CORRECTED VERSION).

    Official Hamilton Specification:
    Bytes 00-01: src module id (2)
    Bytes 02-03: src node id (2)
    Bytes 04-05: src object id (2)
    Bytes 06-07: dst module id (2)
    Bytes 08-09: dst node id (2)
    Bytes 10-11: dst object id (2)
    Bytes 12:    sequence # (1)
    Bytes 13:    reserved (1)
    Bytes 14:    protocol (1)
    Bytes 15:    action (1) - contains response_required bit
    Bytes 16-17: message length (2) - length of optional payload
    Bytes 18-19: options length (2)
    Bytes 20+:   options (x)
    Bytes:       payload (x)
    """
    source_address: ObjectAddress
    dest_address: ObjectAddress
    sequence_number: int  # 1 byte
    reserved: int  # 1 byte - must be 0
    protocol: int  # 1 byte
    action: int  # 1 byte - bit 0 is response_required
    message_length: int  # 2 bytes - length of payload
    options_length: int  # 2 bytes
    options: bytes
    payload: bytes  # Contains HOI2 command data

    def to_bytes(self) -> bytes:
        """Serialize HARP2 packet to bytes according to official Hamilton spec."""
        return (self.source_address.to_bytes() +
                self.dest_address.to_bytes() +
                struct.pack('<BBBBHH',
                           self.sequence_number,
                           self.reserved,
                           self.protocol,
                           self.action,
                           self.message_length,
                           self.options_length) +
                self.options +
                self.payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Harp2':
        """Deserialize HARP2 packet from bytes according to official Hamilton spec."""
        if len(data) < 20:  # 6 + 6 + 8 bytes minimum
            raise ValueError("HARP2 packet too short")

        source_address = ObjectAddress.from_bytes(data[:6])
        dest_address = ObjectAddress.from_bytes(data[6:12])
        sequence_number, reserved, protocol, action, message_length, options_length = struct.unpack('<BBBBHH', data[12:20])

        # Extract options and payload
        options = data[20:20+options_length]
        payload = data[20+options_length:]

        return cls(source_address, dest_address, sequence_number, reserved, protocol, action,
                  message_length, options_length, options, payload)

    @property
    def payload_offset(self) -> int:
        """Get the byte offset where HOI2 payload starts."""
        return 20 + len(self.options)  # Header is 20 bytes + options length

    @property
    def response_required(self) -> bool:
        """Check if response is required (bit 0 of action field)."""
        return bool(self.action & 0x01)

    @response_required.setter
    def response_required(self, value: bool):
        """Set response required bit (bit 0 of action field)."""
        if value:
            self.action |= 0x01
        else:
            self.action &= ~0x01


@dataclass
class Hoi2:
    """Hamilton HOI2 - Application layer command data (CORRECTED VERSION).

    Official Hamilton Specification:
    Bytes 00:    interface ID (1)
    Bytes 01:    action (1) - bit 0 is "response required"
    Bytes 02-03: action ID (2)
    Bytes 04:    minor | major version (.5)|(.5)
    Bytes 05:    number of fragments (1)
    Bytes 06+:   fragments (x)
    """
    interface_id: int  # 1 byte
    action: int  # 1 byte - bit 0 is response_required
    action_id: int  # 2 bytes
    version_major: int  # 4 bits
    version_minor: int  # 4 bits
    number_of_fragments: int  # 1 byte
    fragments: bytes

    def to_bytes(self) -> bytes:
        """Serialize HOI2 command data to bytes according to official Hamilton spec."""
        # Pack version as minor|major (4 bits each)
        version_byte = (self.version_minor & 0xF) | ((self.version_major & 0xF) << 4)

        return (struct.pack('<BBHB',
                           self.interface_id,
                           self.action,
                           self.action_id,
                           version_byte) +
                struct.pack('<B', self.number_of_fragments) +
                self.fragments)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Hoi2':
        """Deserialize HOI2 command data from bytes according to official Hamilton spec."""
        if len(data) < 6:
            raise ValueError("HOI2 command data too short")

        interface_id, action, action_id, version_byte = struct.unpack('<BBHB', data[:5])
        number_of_fragments = struct.unpack('<B', data[5:6])[0]

        # Extract minor/major version from packed byte
        version_minor = version_byte & 0xF
        version_major = (version_byte >> 4) & 0xF

        fragments = data[6:]

        return cls(interface_id, action, action_id, version_major, version_minor,
                  number_of_fragments, fragments)

    @property
    def response_required(self) -> bool:
        """Check if response is required (bit 0 of action field)."""
        return bool(self.action & 0x01)

    @response_required.setter
    def response_required(self, value: bool):
        """Set response required bit (bit 0 of action field)."""
        if value:
            self.action |= 0x01
        else:
            self.action &= ~0x01


@dataclass
class ConnectionPacket:
    """Hamilton ConnectionPacket2 - Special packet for initialization protocol.

    This is used specifically for Protocol 7 (INITIALIZATION) and has a different
    structure than the standard HARP2/HOI2 packets.

    Official Hamilton Specification (from connectionPacket.png):
    Bytes 00-01: size (2 bytes) - does NOT include size field itself
    Bytes 02:    protocol (1 byte) - always 7 for INITIALIZATION
    Bytes 03:    minor | major version (.5)|(.5) - split into two 4-bit fields
    Bytes 04-05: options length (2 bytes)
    Bytes 06+:   options (x bytes)
    Bytes:       connection parameters
    """
    packet_size: int
    protocol: HamiltonProtocol  # Always INITIALIZATION (7)
    version_major: int  # 4 bits
    version_minor: int  # 4 bits
    options_length: int
    options: bytes
    connection_parameters: bytes

    def to_bytes(self) -> bytes:
        """Serialize ConnectionPacket2 to bytes according to official Hamilton spec."""
        # Calculate total packet size: protocol(1) + version(1) + options_length(2) + options(x) + parameters(y)
        # Size field does NOT include itself
        total_size = 1 + 1 + 2 + len(self.options) + len(self.connection_parameters)

        # Pack version as minor|major (4 bits each)
        version_byte = (self.version_minor & 0xF) | ((self.version_major & 0xF) << 4)

        return struct.pack('<HBBH',
                          total_size,
                          self.protocol,
                          version_byte,
                          len(self.options)) + self.options + self.connection_parameters

    @classmethod
    def from_bytes(cls, data: bytes) -> 'ConnectionPacket':
        """Deserialize ConnectionPacket2 from bytes according to official Hamilton spec."""
        if len(data) < 6:  # H + B + B + H = 6 bytes minimum
            raise ValueError("ConnectionPacket2 too short")

        packet_size, protocol, version_byte, options_length = struct.unpack('<HBBH', data[:6])

        # Extract minor/major version from packed byte
        version_minor = version_byte & 0xF
        version_major = (version_byte >> 4) & 0xF

        # Extract options and connection parameters
        options = data[6:6+options_length]
        connection_parameters = data[6+options_length:]

        return cls(packet_size, HamiltonProtocol(protocol), version_major, version_minor, options_length, options, connection_parameters)

    @property
    def payload_offset(self) -> int:
        """Get the byte offset where connection parameters start."""
        return 6 + len(self.options)  # Header is 6 bytes + options length


@dataclass
class HamiltonMessage:
    """Complete Hamilton message with corrected three-layer structure: IpPacket[HARP2[HOI2]]"""
    ip_packet: IpPacket      # Layer 1: Transport
    harp2: Harp2            # Layer 2: Protocol
    hoi2: Hoi2              # Layer 3: Application

    def to_bytes(self) -> bytes:
        """Serialize complete message from inside out."""
        # Build HOI2 command data
        hoi_data = self.hoi2.to_bytes()

        # Build HARP2 packet with HOI2 as payload
        harp_data = Harp2(
            source_address=self.harp2.source_address,
            dest_address=self.harp2.dest_address,
            sequence_number=self.harp2.sequence_number,
            reserved=self.harp2.reserved,
            protocol=self.harp2.protocol,
            action=self.harp2.action,
            message_length=len(hoi_data),
            options_length=len(self.harp2.options),
            options=self.harp2.options,
            payload=hoi_data
        ).to_bytes()

        # Build IpPacket with HARP2 as payload
        ip_data = IpPacket(
            packet_size=len(harp_data),
            protocol=self.ip_packet.protocol,
            version_major=self.ip_packet.version_major,
            version_minor=self.ip_packet.version_minor,
            options=self.ip_packet.options,
            payload=harp_data
        ).to_bytes()

        return ip_data

    @classmethod
    def from_bytes(cls, data: bytes) -> 'HamiltonMessage':
        """Deserialize complete message from outside in."""
        # Parse IpPacket (outermost layer)
        ip_packet = IpPacket.from_bytes(data)

        # Parse HARP2 from IpPacket payload
        harp_data = ip_packet.payload
        harp2 = Harp2.from_bytes(harp_data)

        # Parse HOI2 from HARP2 payload
        hoi_data = harp2.payload
        hoi2 = Hoi2.from_bytes(hoi_data)

        return cls(ip_packet, harp2, hoi2)


# ============================================================================
# PROTOCOL MESSAGE BUILDERS
# ============================================================================

class ProtocolMessageBuilder(ABC):
    """Abstract base for protocol-specific message builders."""

    @abstractmethod
    def build_message(self, command: 'HamiltonCommand') -> bytes:
        """Build message for sending to instrument."""
        pass

    @abstractmethod
    def parse_response(self, data: bytes, command: 'HamiltonCommand') -> dict:
        """Parse response from instrument."""
        pass


class InitializationMessageBuilder(ProtocolMessageBuilder):
    """Builds/parses 2-layer ConnectionPacket messages (Protocol 7)."""

    def build_message(self, command: 'HamiltonCommand') -> bytes:
        """Build initialization message using ConnectionPacket."""
        parameters = command.build_parameters()
        connection_packet = ConnectionPacket(
            packet_size=0,  # Will be calculated in to_bytes()
            protocol=HamiltonProtocol.INITIALIZATION,
            version_major=command.version_major,
            version_minor=command.version_minor,
            options_length=0,
            options=b'',
            connection_parameters=parameters
        )
        return connection_packet.to_bytes()

    def parse_response(self, data: bytes, command: 'HamiltonCommand') -> dict:
        """Parse initialization response from ConnectionPacket."""
        connection_packet = ConnectionPacket.from_bytes(data)
        parsed_params = command.parse_response_parameters(connection_packet.connection_parameters)
        return {
            **parsed_params,
            'protocol': connection_packet.protocol,
            'version_major': connection_packet.version_major,
            'version_minor': connection_packet.version_minor,
        }


class HamiltonMessageBuilder(ProtocolMessageBuilder):
    """Builds/parses 3-layer HamiltonMessage (IpPacket[Harp2[Hoi2]]) for Protocols 2, 3, 6."""

    def build_message(self, command: 'HamiltonCommand') -> bytes:
        """Build 3-layer message from command."""
        # Validate required fields are set
        if command.command_id is None:
            raise ValueError(f"{command.__class__.__name__} must define command_id")
        if command.interface_id is None:
            raise ValueError(f"{command.__class__.__name__} must define interface_id")
        if command.call_type is None:
            raise ValueError(f"{command.__class__.__name__} must define call_type")

        # Build from inside out: HOI2 -> HARP2 -> IpPacket

        # Layer 3: HOI2 (Application)
        parameters = command.build_parameters()
        hoi2 = Hoi2(
            interface_id=command.interface_id,
            action=0,  # Will be set by command if needed
            action_id=command.command_id,
            version_major=command.version_major if hasattr(command, 'version_major') else 0,
            version_minor=command.version_minor if hasattr(command, 'version_minor') else 0,
            number_of_fragments=0,  # Will be calculated from parameters
            fragments=parameters
        )

        # Layer 2: HARP2 (Protocol)
        hoi2_bytes = hoi2.to_bytes()
        harp2 = Harp2(
            source_address=command.source_address if hasattr(command, 'source_address') else ObjectAddress(0, 0, 0),
            dest_address=command.dest_address if hasattr(command, 'dest_address') else ObjectAddress(0, 0, 0),
            sequence_number=command.sequence_number,
            reserved=0,
            protocol=command.call_type,  # Map call_type to protocol field
            action=0,  # Will be set by command if needed
            message_length=len(hoi2_bytes),
            options_length=0,
            options=b'',
            payload=hoi2_bytes
        )

        # Layer 1: IpPacket (Transport)
        harp2_bytes = harp2.to_bytes()
        ip_packet = IpPacket(
            packet_size=0,  # Will be calculated in to_bytes()
            protocol=command.protocol,
            version_major=command.version_major if hasattr(command, 'version_major') else 0,
            version_minor=command.version_minor if hasattr(command, 'version_minor') else 0,
            options=b'',
            payload=harp2_bytes
        )

        # Create complete message
        message = HamiltonMessage(ip_packet, harp2, hoi2)
        return message.to_bytes()

    def parse_response(self, data: bytes, command: 'HamiltonCommand') -> dict:
        """Parse 3-layer response from instrument."""
        # Parse from outside in: IpPacket -> HARP2 -> HOI2
        message = HamiltonMessage.from_bytes(data)

        # Parse HOI2 fragments using command-specific logic
        parsed_params = command.parse_response_parameters(message.hoi2.fragments)

        return {
            **parsed_params,
            'protocol': message.ip_packet.protocol,
            'version_major': message.ip_packet.version_major,
            'version_minor': message.ip_packet.version_minor,
            'sequence_number': message.harp2.sequence_number,
            'interface_id': message.hoi2.interface_id,
            'action_id': message.hoi2.action_id,
        }


# Protocol builder registry
PROTOCOL_BUILDERS = {
    HamiltonProtocol.INITIALIZATION: InitializationMessageBuilder(),
    HamiltonProtocol.OBJECT_DISCOVERY: HamiltonMessageBuilder(),
    HamiltonProtocol.PIPETTE: HamiltonMessageBuilder(),
    HamiltonProtocol.REGISTRATION: HamiltonMessageBuilder(),
}


# ============================================================================
# COMMAND ARCHITECTURE
# ============================================================================

class HamiltonCommand:
    """Base class for Hamilton commands."""

    protocol: HamiltonProtocol
    command_id: int = None  # Must be set by subclasses
    interface_id: int = None  # Must be set by subclasses
    call_type: int = None  # Must be set by subclasses

    def __init__(self, sequence_number: int = 0):
        self.sequence_number = sequence_number

    def build_parameters(self) -> bytes:
        """Override this method to build command-specific parameters."""
        raise NotImplementedError

    def build(self) -> bytes:
        """Build complete message using protocol-specific builder."""
        builder = PROTOCOL_BUILDERS.get(self.protocol)
        if builder is None:
            raise ValueError(f"No builder defined for protocol {self.protocol}")
        return builder.build_message(self)

    @classmethod
    def parse_response(cls, data: bytes) -> dict:
        """Parse response using protocol-specific builder."""
        # Parse outer packet to determine protocol
        if len(data) < 6:
            raise ValueError("Response too short")

        packet_size, protocol = struct.unpack('<HB', data[:3])
        protocol_enum = HamiltonProtocol(protocol)

        builder = PROTOCOL_BUILDERS.get(protocol_enum)
        if builder is None:
            raise ValueError(f"No parser defined for protocol {protocol_enum}")

        return builder.parse_response(data, cls)

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Override this method to parse command-specific response parameters."""
        raise NotImplementedError


# ============================================================================
# SPECIFIC COMMAND IMPLEMENTATIONS
# ============================================================================

class InitializeCommand(HamiltonCommand):
    """Initialize connection to Hamilton instrument.

    Uses ConnectionPacket (2-layer: IpPacket[Parameters]) via InitializationMessageBuilder.
    """

    protocol = HamiltonProtocol.INITIALIZATION
    version_major = 3  # Version 3.0
    version_minor = 0  # Version 3.0

    def __init__(self, sequence_number: int = 0):
        super().__init__(sequence_number)

        # Validate input
        if not isinstance(sequence_number, int):
            raise TypeError(f"sequence_number must be int, got {type(sequence_number)}")
        if sequence_number < 0 or sequence_number > 65535:
            raise ValueError(f"sequence_number must be 0-65535, got {sequence_number}")

        # Standard Hamilton values
        self.connection_type: int = 4369
        self.timeout: int = 300

    def build_parameters(self) -> bytes:
        """Build initialization-specific parameters."""
        # Frame: version, message_id, parameter_count, unknown
        frame = struct.pack('<BBBB', 0, 0, 3, 0)

        # Parameters: id, meta, code, value
        params = struct.pack('<BBHH', 1, 16, 0, 0)  # Request connection ID
        params += struct.pack('<BBHH', 2, 16, 0, self.connection_type)
        params += struct.pack('<BBHH', 4, 16, 0, self.timeout)

        result = frame + params
        logger.debug(f"Built initialization parameters: {len(result)} bytes")
        return result

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict[str, int]:
        """Parse initialization response parameters from Hamilton instrument.

        This method parses the response data received from the Hamilton instrument
        after sending an initialization request. The response contains the assigned
        client_id and connection details.

        Args:
            data: Raw response bytes from Hamilton instrument

        Returns:
            dict[str, int]: Parsed response fields:
                - client_id: Assigned client ID from instrument
                - connection_type: Confirmed connection type
                - timeout: Confirmed timeout value
                - version: Protocol version
                - count: Number of parameters in response

        Raises:
            ValueError: If response format is invalid or client_id is missing
        """
        # Parse response frame
        version, message_id, count, unknown = struct.unpack('<BBBB', data[:4])

        if message_id != 0:
            raise ValueError(f"Expected message ID 0, got {message_id}")

        # Parse response parameters
        client_id = 0
        connection_type = 0
        timeout = 0

        offset = 4
        for _ in range(count):
            parameter, meta, code, value = struct.unpack('<BBHH', data[offset:offset+6])
            offset += 6

            if meta != 17:  # Response uses meta=17 (different from request meta=16)
                raise ValueError(f"Expected meta 17 in response, got {meta}")
            if code != 0:
                raise ValueError(f"Expected code 0, got {code}")

            if parameter == 1:
                client_id = value
            elif parameter == 2:
                connection_type = value
            elif parameter == 4:
                timeout = value

        if client_id == 0:
            raise ValueError("No client ID found in response")

        return {
            'client_id': client_id,
            'connection_type': connection_type,
            'timeout': timeout,
            'version': version,
            'count': count,
        }


# ============================================================================
# TCP CONNECTION
# ============================================================================

class HamiltonTCPConnection(TCP):
    """Hamilton-specific TCP connection with protocol support."""

    def __init__(
        self,
        host: str,
        port: int,
        read_timeout: int = 30,
        write_timeout: int = 30,
        buffer_size: int = 1024,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 3,
    ):
        super().__init__(
            host=host,
            port=port,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            buffer_size=buffer_size,
            auto_reconnect=auto_reconnect,
            max_reconnect_attempts=max_reconnect_attempts,
        )

        # Hamilton-specific state
        self._message_handlers: Dict[HamiltonProtocol, Callable[[bytes], None]] = {}
        self._on_message_callback: Optional[Callable[[bytes], None]] = None
        self._message_queue: Optional[asyncio.Queue] = None

        # Protocol channel management
        self._protocol_channels: Dict[int, asyncio.Queue] = {}
        self._client_address: Optional[ObjectAddress] = None


    async def setup(self):
        """Initialize Hamilton TCP connection."""
        await super().setup()
        logger.debug("Hamilton TCP connection setup complete")


    def register_protocol(self, protocol_id: int) -> asyncio.Queue:
        """Register queue for specific protocol responses."""
        if protocol_id not in self._protocol_channels:
            self._protocol_channels[protocol_id] = asyncio.Queue(maxsize=100)
        return self._protocol_channels[protocol_id]

    def unregister_protocol(self, protocol_id: int):
        """Unregister protocol queue."""
        if protocol_id in self._protocol_channels:
            del self._protocol_channels[protocol_id]

    def set_client_address(self, address: ObjectAddress):
        """Set the client address for requests."""
        self._client_address = address

    async def read_hamilton_packet(self) -> bytes:
        """Read complete Hamilton IpPacket using two-step approach.

        Step 1: Read 2 bytes for packet size
        Step 2: Read packet_size bytes for payload
        """
        # Step 1: Read packet size (2 bytes, little-endian)
        size_data = await self.read_exact(2)
        packet_size = struct.unpack('<H', size_data)[0]

        # Step 2: Read payload
        payload_data = await self.read_exact(packet_size)

        # Return complete packet (size + payload)
        return size_data + payload_data

    def serialize(self) -> dict:
        """Serialize the Hamilton TCP connection."""
        return {
            **super().serialize(),
            "type": "hamilton_tcp",
        }


# ============================================================================
# HIGH-LEVEL API
# ============================================================================

class HamiltonInstrument:
    """High-level Hamilton instrument control."""

    def __init__(self, connection: HamiltonTCPConnection):
        self.connection = connection

    async def execute_command(self, command_class, timeout: float = 10.0, **kwargs) -> dict:
        """Execute Hamilton command with direct two-step read."""
        # Create and build command
        cmd = command_class(**kwargs)
        logger.debug(f"Created {command_class.__name__} command")

        try:
            command_message = cmd.build()
            logger.debug(f"Built message: {len(command_message)} bytes")
        except Exception as e:
            logger.error(f"Error building message: {e}")
            raise

        # Send command
        await self.connection.write(command_message)
        logger.debug(f"Sent message to instrument")

        # Read response using two-step approach with timeout
        try:
            response = await asyncio.wait_for(
                self.connection.read_hamilton_packet(),
                timeout=timeout
            )
            logger.debug(f"Received response: {len(response)} bytes")
            return command_class.parse_response(response)
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for response")
            raise

    async def initialize(self) -> dict:
        """Initialize connection to Hamilton instrument."""
        result = await self.execute_command(InitializeCommand)

        # Set client address for future requests
        client_address = ObjectAddress(module_id=0, node_id=0, object_id=result['client_id'])
        self.connection.set_client_address(client_address)

        return result
