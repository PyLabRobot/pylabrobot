"""Command layer for Hamilton TCP.

HamiltonCommand base: build_parameters() returns HoiParams; interpret_response()
auto-decodes success responses via nested Response dataclasses (wire-type
annotations and parse_into_struct). Wire → HoiParams → Packets → Messages → Commands.
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import I32


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
              return HoiParams().add(self.value, I32)

          @classmethod
          def parse_response_parameters(cls, data: bytes) -> dict:
              parser = HoiParamsParser(data)
              _, result = parser.parse_next()
              return {'result': result}
  """

  # Class-level attributes that subclasses must override
  protocol: Optional[HamiltonProtocol] = None
  interface_id: Optional[int] = None
  command_id: Optional[int] = None

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

  def get_log_params(self) -> dict:
    """Get parameters to log for this command.

    Lazily computes the parameters by inspecting the __init__ signature
    and reading current attribute values from self.

    Subclasses can override to customize formatting (e.g., unit conversions,
    array truncation).

    Returns:
      Dictionary of parameter names to values
    """
    exclude = {"self", "dest"}
    sig = inspect.signature(type(self).__init__)
    params = {}
    for param_name in sig.parameters:
      if param_name not in exclude and hasattr(self, param_name):
        params[param_name] = getattr(self, param_name)
    return params

  def build(
    self, src: Optional[Address] = None, seq: Optional[int] = None, response_required: bool = True
  ) -> bytes:
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

    # Ensure required attributes are set (they should be by subclasses)
    if self.interface_id is None:
      raise ValueError(f"{self.__class__.__name__} must define interface_id")
    if self.command_id is None:
      raise ValueError(f"{self.__class__.__name__} must define command_id")

    # Build parameters using command-specific logic
    params = self.build_parameters()

    # Create CommandMessage and set parameters directly
    # This avoids wasteful serialization/parsing round-trip
    msg = CommandMessage(
      dest=self.dest,
      interface_id=self.interface_id,
      method_id=self.command_id,
      params=params,
      action_code=self.action_code,
      harp_protocol=self.harp_protocol,
      ip_protocol=self.ip_protocol,
    )

    # Build final packet
    return msg.build(source, sequence, harp_response_required=response_required)

  def interpret_response(self, response: CommandResponse) -> Any:
    """Interpret success response (command layer auto-decode).

    If the command class defines a nested Response dataclass with wire-type
    annotations, decode via parse_into_struct and return a Response instance.
    Otherwise fall back to parse_response_parameters (dict or None).

    Args:
      response: CommandResponse from network

    Returns:
      Command.Response instance, dict, or None
    """
    cls = type(self)
    if hasattr(cls, "Response") and response.hoi.params:
      from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
        HoiParamsParser,
        parse_into_struct,
      )

      return parse_into_struct(HoiParamsParser(response.hoi.params), cls.Response)
    return self.parse_response_parameters(response.hoi.params)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> Optional[dict]:
    """Parse response parameters from HOI payload.

    Override this method in subclasses to parse command-specific responses.

    Args:
      data: Raw bytes from HOI fragments field

    Returns:
      Dictionary with parsed response data, or None if no data to extract
    """
    return None
