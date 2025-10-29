"""Hamilton command architecture using new simplified TCP stack.

This module provides the HamiltonCommand base class that uses the new refactored
architecture: Wire → HoiParams → Packets → Messages → Commands.
"""

from __future__ import annotations

from typing import Optional

from .protocol import HamiltonProtocol
from .packets import Address
from .messages import CommandMessage, CommandResponse
from .hoi_params import HoiParams, HoiParamsParser


class HamiltonCommand:
    """Base class for Hamilton commands using new simplified architecture.

    This replaces the old HamiltonCommand from tcp_codec.py with a cleaner design:
    - Explicitly uses CommandMessage for building packets
    - build_parameters() returns HoiParams object (not bytes)
    - Uses Address instead of ObjectAddress
    - Cleaner separation of concerns

    Example:
        class MyCommand(HamiltonCommand):
            protocol = HamiltonProtocol.OBJECT_DISCOVERY
            interface_id = 0
            command_id = 42

            def __init__(self, dest: Address, value: int):
                super().__init__(dest)
                self.value = value

            def build_parameters(self) -> HoiParams:
                return HoiParams().i32(self.value)

            @classmethod
            def parse_response_parameters(cls, data: bytes) -> dict:
                parser = HoiParamsParser(data)
                _, result = parser.parse_next()
                return {'result': result}
    """

    # Class-level attributes that subclasses must override
    protocol: HamiltonProtocol = None
    interface_id: int = None
    command_id: int = None

    # Action configuration (can be overridden by subclasses)
    action_code: int = 3  # Default: COMMAND_REQUEST
    harp_protocol: int = 2  # Default: HOI2
    ip_protocol: int = 6  # Default: OBJECT_DISCOVERY

    def __init__(self, dest: Address):
        """Initialize Hamilton command.

        Args:
            dest: Destination address for this command
        """
        if self.protocol is None:
            raise ValueError(f"{self.__class__.__name__} must define protocol")
        if self.interface_id is None:
            raise ValueError(f"{self.__class__.__name__} must define interface_id")
        if self.command_id is None:
            raise ValueError(f"{self.__class__.__name__} must define command_id")

        self.dest = dest
        self.dest_address = dest  # Alias for compatibility
        self.sequence_number = 0
        self.source_address: Optional[Address] = None

    def build_parameters(self) -> HoiParams:
        """Build HOI parameters for this command.

        Override this method in subclasses to provide command-specific parameters.
        Return a HoiParams object (not bytes!).

        Returns:
            HoiParams object with command parameters
        """
        return HoiParams()

    def build(self, src: Optional[Address] = None, seq: Optional[int] = None, response_required: bool = True) -> bytes:
        """Build complete Hamilton message using CommandMessage.

        Args:
            src: Source address (uses self.source_address if None)
            seq: Sequence number (uses self.sequence_number if None)
            response_required: Whether a response is expected

        Returns:
            Complete packet bytes ready to send over TCP
        """
        # Use instance attributes if not provided
        source = src if src is not None else self.source_address
        sequence = seq if seq is not None else self.sequence_number

        if source is None:
            raise ValueError("Source address not set - backend should set this before building")

        # Build parameters using command-specific logic
        params = self.build_parameters()

        # Create CommandMessage and add parameters
        msg = CommandMessage(
            dest=self.dest,
            interface_id=self.interface_id,
            method_id=self.command_id,
            action_code=self.action_code,
            harp_protocol=self.harp_protocol,
            ip_protocol=self.ip_protocol
        )

        # Transfer all parameters from HoiParams to CommandMessage
        # We do this by getting the built bytes and creating a new HoiParams from them
        params_bytes = params.build()
        if params_bytes:
            # Parse back to get individual params and add them to message
            parser = HoiParamsParser(params_bytes)
            while parser.has_remaining():
                type_id, value = parser.parse_next()
                # Add each parameter to the message based on type_id
                self._add_param_to_message(msg, type_id, value)

        # Build final packet
        return msg.build(source, sequence, harp_response_required=response_required)

    def _add_param_to_message(self, msg: CommandMessage, type_id: int, value):
        """Helper to add parsed parameter to CommandMessage."""
        from .protocol import HamiltonDataType

        # Map type_id to CommandMessage method
        type_map = {
            HamiltonDataType.I8: msg.add_i8,
            HamiltonDataType.I16: msg.add_i16,
            HamiltonDataType.I32: msg.add_i32,
            HamiltonDataType.I64: msg.add_i64,
            HamiltonDataType.U8: msg.add_u8,
            HamiltonDataType.U16: msg.add_u16,
            HamiltonDataType.U32: msg.add_u32,
            HamiltonDataType.U64: msg.add_u64,
            HamiltonDataType.F32: msg.add_f32,
            HamiltonDataType.F64: msg.add_f64,
            HamiltonDataType.STRING: msg.add_string,
            HamiltonDataType.BOOL: msg.add_bool,
            HamiltonDataType.I32_ARRAY: msg.add_i32_array,
            HamiltonDataType.U32_ARRAY: msg.add_u32_array,
            HamiltonDataType.STRING_ARRAY: msg.add_string_array,
        }

        if type_id in type_map:
            type_map[type_id](value)
        else:
            raise ValueError(f"Unsupported type_id in command: {type_id}")

    def interpret_response(self, response: 'SuccessResponse') -> dict:
        """Interpret success response using typed response object.

        This is the new interface used by the backend. Default implementation
        delegates to parse_response_parameters for backwards compatibility.

        Args:
            response: Typed SuccessResponse from ResponseParser

        Returns:
            Dictionary with parsed response data
        """
        return self.parse_response_parameters(response.raw_params)

    def parse_response_from_message(self, message: CommandResponse) -> dict:
        """Parse response from CommandResponse (legacy interface).

        Args:
            message: Parsed CommandResponse from messages.py

        Returns:
            Dictionary with parsed response data
        """
        # Extract HOI parameters and parse using command-specific logic
        return self.parse_response_parameters(message.hoi_params)

    @classmethod
    def parse_response_parameters(cls, data: bytes) -> dict:
        """Parse response parameters from HOI payload.

        Override this method in subclasses to parse command-specific responses.

        Args:
            data: Raw bytes from HOI fragments field

        Returns:
            Dictionary with parsed response data
        """
        raise NotImplementedError(f"{cls.__name__} must implement parse_response_parameters()")

