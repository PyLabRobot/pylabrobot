"""High-level Hamilton message builders and response parsers.

This module provides user-facing message builders and their corresponding
response parsers. Each message type is paired with its response type:

Request Builders:
- InitMessage: Builds IP[Connection] for initialization
- RegistrationMessage: Builds IP[HARP[Registration]] for discovery
- CommandMessage: Builds IP[HARP[HOI]] for method calls

Response Parsers:
- InitResponse: Parses initialization responses
- RegistrationResponse: Parses registration responses
- CommandResponse: Parses command responses

This pairing creates symmetry and makes correlation explicit.

Example:
    # Build and send
    msg = CommandMessage(dest, interface_id=0, method_id=42)
    msg.add_i32(100)
    packet_bytes = msg.build(src, seq=1)

    # Parse response
    response = CommandResponse.from_bytes(received_bytes)
    params = response.hoi_params
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .hoi_params import HoiParams
from .packets import (
    Address,
    ConnectionPacket,
    HarpPacket,
    HoiPacket,
    IpPacket,
    RegistrationPacket,
)
from .protocol import HarpTransportableProtocol, RegistrationOptionType
from .wire import Wire


class CommandMessage:
    """Build HOI command messages for method calls.

    Creates complete IP[HARP[HOI]] packets with proper protocols and actions.
    Parameters are automatically wrapped with DataFragment headers via HoiParams.

    Example:
        msg = CommandMessage(dest, interface_id=0, method_id=42)
        msg.add_i32(100).add_string("test")
        packet_bytes = msg.build(src, seq=1)
    """

    def __init__(
        self,
        dest: Address,
        interface_id: int,
        method_id: int,
        action_code: int = 3,  # Default: COMMAND_REQUEST
        harp_protocol: int = 2,  # Default: HOI2
        ip_protocol: int = 6  # Default: OBJECT_DISCOVERY
    ):
        """Initialize command message.

        Args:
            dest: Destination object address
            interface_id: Interface ID (typically 0 for main interface, 1 for extended)
            method_id: Method/action ID to invoke
            action_code: HOI action code (default 3=COMMAND_REQUEST)
            harp_protocol: HARP protocol identifier (default 2=HOI2)
            ip_protocol: IP protocol identifier (default 6=OBJECT_DISCOVERY)
        """
        self.dest = dest
        self.interface_id = interface_id
        self.method_id = method_id
        self.action_code = action_code
        self.harp_protocol = harp_protocol
        self.ip_protocol = ip_protocol
        self.params = HoiParams()

    # Convenience methods for adding parameters
    def add_i8(self, value: int) -> 'CommandMessage':
        """Add signed 8-bit integer parameter."""
        self.params.i8(value)
        return self

    def add_i16(self, value: int) -> 'CommandMessage':
        """Add signed 16-bit integer parameter."""
        self.params.i16(value)
        return self

    def add_i32(self, value: int) -> 'CommandMessage':
        """Add signed 32-bit integer parameter."""
        self.params.i32(value)
        return self

    def add_i64(self, value: int) -> 'CommandMessage':
        """Add signed 64-bit integer parameter."""
        self.params.i64(value)
        return self

    def add_u8(self, value: int) -> 'CommandMessage':
        """Add unsigned 8-bit integer parameter."""
        self.params.u8(value)
        return self

    def add_u16(self, value: int) -> 'CommandMessage':
        """Add unsigned 16-bit integer parameter."""
        self.params.u16(value)
        return self

    def add_u32(self, value: int) -> 'CommandMessage':
        """Add unsigned 32-bit integer parameter."""
        self.params.u32(value)
        return self

    def add_u64(self, value: int) -> 'CommandMessage':
        """Add unsigned 64-bit integer parameter."""
        self.params.u64(value)
        return self

    def add_f32(self, value: float) -> 'CommandMessage':
        """Add 32-bit float parameter."""
        self.params.f32(value)
        return self

    def add_f64(self, value: float) -> 'CommandMessage':
        """Add 64-bit double parameter."""
        self.params.f64(value)
        return self

    def add_string(self, value: str) -> 'CommandMessage':
        """Add string parameter."""
        self.params.string(value)
        return self

    def add_bool(self, value: bool) -> 'CommandMessage':
        """Add boolean parameter."""
        self.params.bool(value)
        return self

    def add_i32_array(self, values: list[int]) -> 'CommandMessage':
        """Add array of signed 32-bit integers."""
        self.params.i32_array(values)
        return self

    def add_u32_array(self, values: list[int]) -> 'CommandMessage':
        """Add array of unsigned 32-bit integers."""
        self.params.u32_array(values)
        return self

    def add_string_array(self, values: list[str]) -> 'CommandMessage':
        """Add array of strings."""
        self.params.string_array(values)
        return self

    def build(self, src: Address, seq: int,
              harp_response_required: bool = True,
              hoi_response_required: bool = False) -> bytes:
        """Build complete IP[HARP[HOI]] packet.

        Args:
            src: Source address (client address)
            seq: Sequence number for this request
            harp_response_required: Set bit 4 in HARP action byte (default True)
            hoi_response_required: Set bit 4 in HOI action byte (default False)

        Returns:
            Complete packet bytes ready to send over TCP
        """
        # Build HOI - it handles its own action byte construction
        hoi = HoiPacket(
            interface_id=self.interface_id,
            action_code=self.action_code,
            action_id=self.method_id,
            params=self.params.build(),
            response_required=hoi_response_required
        )

        # Build HARP - it handles its own action byte construction
        harp = HarpPacket(
            src=src,
            dst=self.dest,
            seq=seq,
            protocol=self.harp_protocol,
            action_code=self.action_code,
            payload=hoi.pack(),
            response_required=harp_response_required
        )

        # Wrap in IP packet
        ip = IpPacket(
            protocol=self.ip_protocol,
            payload=harp.pack()
        )

        return ip.pack()


class RegistrationMessage:
    """Build Registration messages for object discovery.

    Creates complete IP[HARP[Registration]] packets for discovering modules,
    objects, and capabilities on the Hamilton instrument.

    Example:
        msg = RegistrationMessage(dest, action_code=12)
        msg.add_registration_option(RegistrationOptionType.HARP_PROTOCOL_REQUEST, protocol=2, request_id=1)
        packet_bytes = msg.build(src, req_addr, res_addr, seq=1)
    """

    def __init__(
        self,
        dest: Address,
        action_code: int,
        response_code: int = 0,  # Default: no error
        harp_protocol: int = 3,  # Default: Registration
        ip_protocol: int = 6  # Default: OBJECT_DISCOVERY
    ):
        """Initialize registration message.

        Args:
            dest: Destination address (typically 0:0:65534 for registration service)
            action_code: Registration action code (e.g., 12=HARP_PROTOCOL_REQUEST)
            response_code: Response code (default 0=no error)
            harp_protocol: HARP protocol identifier (default 3=Registration)
            ip_protocol: IP protocol identifier (default 6=OBJECT_DISCOVERY)
        """
        self.dest = dest
        self.action_code = action_code
        self.response_code = response_code
        self.harp_protocol = harp_protocol
        self.ip_protocol = ip_protocol
        self.options = bytearray()

    def add_registration_option(
        self,
        option_type: RegistrationOptionType,
        protocol: int = 2,
        request_id: int = 1
    ) -> 'RegistrationMessage':
        """Add a registration packet option.

        Args:
            option_type: Type of registration option (from RegistrationOptionType enum)
            protocol: For HARP_PROTOCOL_REQUEST: protocol type (2=HOI, default)
            request_id: For HARP_PROTOCOL_REQUEST: what to discover (1=root, 2=global)

        Returns:
            Self for method chaining
        """
        # Registration option format: [option_id:1][length:1][data...]
        # For HARP_PROTOCOL_REQUEST (option 5): data is [protocol:1][request_id:1]
        data = Wire.write().u8(protocol).u8(request_id).finish()
        option = Wire.write().u8(option_type).u8(len(data)).bytes(data).finish()
        self.options.extend(option)
        return self

    def build(
        self,
        src: Address,
        req_addr: Address,
        res_addr: Address,
        seq: int,
        harp_action_code: int = 3,  # Default: COMMAND_REQUEST
        harp_response_required: bool = True  # Default: request with response
    ) -> bytes:
        """Build complete IP[HARP[Registration]] packet.

        Args:
            src: Source address (client address)
            req_addr: Request address (for registration context)
            res_addr: Response address (for registration context)
            seq: Sequence number for this request
            harp_action_code: HARP action code (default 3=COMMAND_REQUEST)
            harp_response_required: Whether response required (default True)

        Returns:
            Complete packet bytes ready to send over TCP
        """
        # Build Registration packet
        reg = RegistrationPacket(
            action_code=self.action_code,
            response_code=self.response_code,
            req_address=req_addr,
            res_address=res_addr,
            options=bytes(self.options)
        )

        # Wrap in HARP packet
        harp = HarpPacket(
            src=src,
            dst=self.dest,
            seq=seq,
            protocol=self.harp_protocol,
            action_code=harp_action_code,
            payload=reg.pack(),
            response_required=harp_response_required
        )

        # Wrap in IP packet
        ip = IpPacket(
            protocol=self.ip_protocol,
            payload=harp.pack()
        )

        return ip.pack()


class InitMessage:
    """Build Connection initialization messages.

    Creates complete IP[Connection] packets for establishing a connection
    with the Hamilton instrument. Uses Protocol 7 (INITIALIZATION) which
    has a different structure than HARP-based messages.

    Example:
        msg = InitMessage(timeout=30)
        packet_bytes = msg.build()
"""

    def __init__(
        self,
        timeout: int = 30,
        connection_type: int = 1,  # Default: standard connection
        protocol_version: int = 0x30,  # Default: 3.0
        ip_protocol: int = 7  # Default: INITIALIZATION
    ):
        """Initialize connection message.

        Args:
            timeout: Connection timeout in seconds (default 30)
            connection_type: Connection type (default 1=standard)
            protocol_version: Protocol version byte (default 0x30=3.0)
            ip_protocol: IP protocol identifier (default 7=INITIALIZATION)
        """
        self.timeout = timeout
        self.connection_type = connection_type
        self.protocol_version = protocol_version
        self.ip_protocol = ip_protocol

    def build(self) -> bytes:
        """Build complete IP[Connection] packet.

        Returns:
            Complete packet bytes ready to send over TCP
        """
        # Build raw connection parameters (NOT DataFragments)
        # Frame: [version:1][message_id:1][count:1][unknown:1]
        # Parameters: [id:1][type:1][reserved:2][value:2] repeated
        params = (Wire.write()
                  # Frame
                  .u8(0)  # version
                  .u8(0)  # message_id
                  .u8(3)  # count (3 parameters)
                  .u8(0)  # unknown
                  # Parameter 1: connection_id (request allocation)
                  .u8(1)   # param id
                  .u8(16)  # param type
                  .u16(0)  # reserved
                  .u16(0)  # value (0 = request allocation)
                  # Parameter 2: connection_type
                  .u8(2)   # param id
                  .u8(16)  # param type
                  .u16(0)  # reserved
                  .u16(self.connection_type)  # value
                  # Parameter 3: timeout
                  .u8(4)   # param id
                  .u8(16)  # param type
                  .u16(0)  # reserved
                  .u16(self.timeout)  # value
                  .finish())

        # Build IP packet
        packet_size = 1 + 1 + 2 + len(params)  # protocol + version + opts_len + params

        return (Wire.write()
                .u16(packet_size)
                .u8(self.ip_protocol)
                .u8(self.protocol_version)
                .u16(0)  # options_length
                .bytes(params)
                .finish())


# ============================================================================
# RESPONSE PARSERS - Paired with message builders above
# ============================================================================


@dataclass
class InitResponse:
    """Parsed initialization response.

    Pairs with InitMessage - parses Protocol 7 (INITIALIZATION) responses.
    """
    raw_bytes: bytes
    client_id: int
    connection_type: int
    timeout: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'InitResponse':
        """Parse initialization response.

        Args:
            data: Raw bytes from TCP socket

        Returns:
            Parsed InitResponse with connection parameters
        """
        # Skip IP header (size + protocol + version + opts_len = 6 bytes)
        parser = Wire.read(data[6:])

        # Parse frame
        version = parser.u8()
        message_id = parser.u8()
        count = parser.u8()
        unknown = parser.u8()

        # Parse parameter 1 (client_id)
        param1_id = parser.u8()
        param1_type = parser.u8()
        param1_reserved = parser.u16()
        client_id = parser.u16()

        # Parse parameter 2 (connection_type)
        param2_id = parser.u8()
        param2_type = parser.u8()
        param2_reserved = parser.u16()
        connection_type = parser.u16()

        # Parse parameter 4 (timeout)
        param4_id = parser.u8()
        param4_type = parser.u8()
        param4_reserved = parser.u16()
        timeout = parser.u16()

        return cls(
            raw_bytes=data,
            client_id=client_id,
            connection_type=connection_type,
            timeout=timeout
        )


@dataclass
class RegistrationResponse:
    """Parsed registration response.

    Pairs with RegistrationMessage - parses IP[HARP[Registration]] responses.
    """
    raw_bytes: bytes
    ip: IpPacket
    harp: HarpPacket
    registration: RegistrationPacket

    @classmethod
    def from_bytes(cls, data: bytes) -> 'RegistrationResponse':
        """Parse registration response.

        Args:
            data: Raw bytes from TCP socket

        Returns:
            Parsed RegistrationResponse with all layers
        """
        ip = IpPacket.unpack(data)
        harp = HarpPacket.unpack(ip.payload)
        registration = RegistrationPacket.unpack(harp.payload)

        return cls(
            raw_bytes=data,
            ip=ip,
            harp=harp,
            registration=registration
        )

    @property
    def sequence_number(self) -> int:
        """Get sequence number from HARP layer."""
        return self.harp.seq


@dataclass
class CommandResponse:
    """Parsed command response.

    Pairs with CommandMessage - parses IP[HARP[HOI]] responses.
    """
    raw_bytes: bytes
    ip: IpPacket
    harp: HarpPacket
    hoi: HoiPacket

    @classmethod
    def from_bytes(cls, data: bytes) -> 'CommandResponse':
        """Parse command response.

        Args:
            data: Raw bytes from TCP socket

        Returns:
            Parsed CommandResponse with all layers

        Raises:
            ValueError: If response is not HOI protocol
        """
        ip = IpPacket.unpack(data)
        harp = HarpPacket.unpack(ip.payload)

        if harp.protocol != HarpTransportableProtocol.HOI2:
            raise ValueError(f"Expected HOI2 protocol, got {harp.protocol}")

        hoi = HoiPacket.unpack(harp.payload)

        return cls(
            raw_bytes=data,
            ip=ip,
            harp=harp,
            hoi=hoi
        )

    @property
    def sequence_number(self) -> int:
        """Get sequence number from HARP layer."""
        return self.harp.seq

    @property
    def hoi_params(self) -> bytes:
        """Get HOI parameters (DataFragment-wrapped)."""
        return self.hoi.params


# ============================================================================
# TYPED HOI RESPONSE CLASSES - For response dispatch
# ============================================================================


@dataclass
class HoiResponse:
    """Base class for typed HOI responses with action-based dispatch.

    Provides type-safe access to response data with proper error handling.
    """
    action: int  # Hoi2Action enum value
    interface_id: int
    action_id: int
    raw_params: bytes
    response_required: bool  # Extracted from bit 4 of action byte


@dataclass
class SuccessResponse(HoiResponse):
    """Successful HOI response (action 0x01 or 0x04)."""
    pass


@dataclass
class ErrorResponse(HoiResponse):
    """Error HOI response (action 0x02, 0x05, or 0x0a).

    Contains parsed error details from the response.
    """
    error_code: int
    error_message: str


class ResponseParser:
    """Parse CommandResponse into typed HoiResponse objects.

    Provides action-based dispatch with automatic error detection.

    Example:
        parser = ResponseParser()
        response = parser.parse(command_response)
        if isinstance(response, ErrorResponse):
            raise RuntimeError(f"Error {response.error_code}: {response.error_message}")
    """

    def parse(self, cmd_response: CommandResponse) -> HoiResponse:
        """Parse CommandResponse and dispatch based on HOI action code.

        Args:
            cmd_response: Parsed CommandResponse from network

        Returns:
            Typed HoiResponse (SuccessResponse or ErrorResponse)

        Raises:
            ValueError: If action code is unexpected
        """
        from .protocol import Hoi2Action

        # Get action code (lower 4 bits)
        action = Hoi2Action(cmd_response.hoi.action_code)

        # Dispatch based on action type
        if action in (Hoi2Action.STATUS_EXCEPTION,
                      Hoi2Action.COMMAND_EXCEPTION,
                      Hoi2Action.INVALID_ACTION_RESPONSE):
            return self._parse_error(cmd_response, action)
        elif action in (Hoi2Action.STATUS_RESPONSE,
                        Hoi2Action.COMMAND_RESPONSE):
            return SuccessResponse(
                action=action,
                interface_id=cmd_response.hoi.interface_id,
                action_id=cmd_response.hoi.action_id,
                raw_params=cmd_response.hoi.params,
                response_required=cmd_response.hoi.response_required
            )
        else:
            raise ValueError(f"Unexpected HOI action: {action} (0x{action:02x})")

    def _parse_error(self, cmd_response: CommandResponse, action: int) -> ErrorResponse:
        """Parse error response.

        Error responses may have custom formats that don't follow standard
        DataFragment encoding. Return the raw payload as hex for debugging.

        Args:
            cmd_response: Raw command response
            action: HOI action code

        Returns:
            ErrorResponse with error details
        """
        # Error responses don't follow standard DataFragment format
        # Just return the raw data as hex for inspection
        error_code = action  # Use action code as error code
        error_message = f"Error response (action={action:#x}): {cmd_response.hoi.params.hex()}"

        return ErrorResponse(
            action=action,
            interface_id=cmd_response.hoi.interface_id,
            action_id=cmd_response.hoi.action_id,
            raw_params=cmd_response.hoi.params,
            response_required=cmd_response.hoi.response_required,
            error_code=error_code,
            error_message=error_message
        )

