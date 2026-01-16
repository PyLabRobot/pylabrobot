"""Hamilton command architecture using new simplified TCP stack.

This module provides the HamiltonCommand base class that uses the new refactored
architecture: Wire → HoiParams → Packets → Messages → Commands.
"""

from __future__ import annotations

import inspect
from typing import Optional

from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  SuccessResponse,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol


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
    self._log_params: dict = {}  # Initialize empty - will be populated by _assign_params() if called

  def _assign_params(self, exclude: Optional[set] = None):
    """Build logging dict from __init__ parameters.

    This method inspects the __init__ signature and builds a dict of
    parameter values for logging purposes. Attributes should be explicitly
    assigned in __init__ before calling this method.

    Args:
        exclude: Set of parameter names to exclude from logging.
                Defaults to {'self', 'dest'}.

    Note:
        This method must be called from within __init__ after super().__init__()
        and after explicit attribute assignments to access the calling frame's
        local variables.
    """
    exclude = exclude or {"self", "dest"}
    # Use type(self).__init__ to avoid mypy error about accessing __init__ on instance
    sig = inspect.signature(type(self).__init__)
    current_frame = inspect.currentframe()
    if current_frame is None:
      # Frame inspection failed, return empty dict
      self._log_params = {}
      return
    frame = current_frame.f_back
    if frame is None:
      # No calling frame, return empty dict
      self._log_params = {}
      return

    # Build params dict for logging (no assignments - attributes should be set explicitly)
    params = {}
    frame_locals = frame.f_locals
    for param_name in sig.parameters:
      if param_name not in exclude:
        if param_name in frame_locals:
          value = frame_locals[param_name]
          params[param_name] = value

    # Store for logging
    self._log_params = params

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

    Returns the params dict built by _assign_params() during __init__.
    This eliminates duplicate signature inspection and provides efficient
    access to logged parameters.

    Subclasses can override to customize formatting (e.g., unit conversions,
    array truncation).

    Returns:
        Dictionary of parameter names to values (empty dict if _assign_params() not called)
    """
    return self._log_params

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
      action_code=self.action_code,
      harp_protocol=self.harp_protocol,
      ip_protocol=self.ip_protocol,
    )
    msg.set_params(params)

    # Build final packet
    return msg.build(source, sequence, harp_response_required=response_required)

  def interpret_response(self, response: "SuccessResponse") -> dict:
    """Interpret success response using typed response object.

    This is the new interface used by the backend. Default implementation
    directly calls parse_response_parameters for efficiency.

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
