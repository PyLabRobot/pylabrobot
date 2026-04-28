"""Command layer for Hamilton TCP.

TCPCommand base: build_parameters() returns HoiParams; interpret_response()
auto-decodes success responses via nested Response dataclasses (wire-type
annotations and parse_into_struct). Wire → HoiParams → Packets → Messages → Commands.
"""

from __future__ import annotations

import inspect
from dataclasses import fields, is_dataclass
from typing import Any, Optional

from pylabrobot.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  interpret_hoi_success_payload,
  log_hoi_result_entries,
  split_hoi_params_after_warning_prefix,
)
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.tcp.wire_types import HcResultEntry


class TCPCommand:
  """Base class for Hamilton TCP commands.

  This replaces the old command base from tcp_codec.py with a cleaner design:
  - Explicitly uses CommandMessage for building packets
  - build_parameters() returns HoiParams object (not bytes)
  - Uses Address instead of ObjectAddress
  - Cleaner separation of concerns

  Example:
      class MyCommand(TCPCommand):
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
    """Initialize TCP command.

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

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map a ``HcResultEntry`` to a 0-indexed PLR channel, or ``None`` to skip.

    Default: the entry's position in the HoiResult — firmware populates arrays
    in active-channel order. ``NimbusCommand`` / ``PrepCommand`` override this
    to translate the active-channel ordinal into the caller's 0-indexed channel
    via ``channels_involved`` bitmask or per-channel struct-array reflection.
    """
    return entry_index

  def error_entries_use_physical_channels(self) -> bool:
    """Whether ``STATUS_EXCEPTION`` entries should be mapped to PLR channel indices.

    Returns ``True`` when the command carries per-channel wire parameters:
    Prep ``StructArray`` elements with a ``channel`` field, or Nimbus
    ``channels_involved`` parallel arrays. Void MLPrep / status queries return
    ``False`` so the client raises :class:`~pylabrobot.hamilton.tcp.hoi_error.HoiError`
    instead of attributing errors to synthetic ``ch0``.
    """
    if not is_dataclass(self):
      return False
    for f in fields(self):
      if f.name == "channels_involved":
        return True
      value = getattr(self, f.name, None)
      if isinstance(value, list) and value:
        if getattr(value[0], "channel", None) is not None:
          return True
    return False

  def interpret_response(self, response: CommandResponse) -> Any:
    """Pure decoder for a success response — never raises on channel errors.

    For ``STATUS_WARNING`` / ``COMMAND_WARNING`` frames, strips the leading
    summary + formatted-string prefix (per ``SystemController.SendAndReceive``)
    and logs entries parsed via ``HoiDecoder2.GetHcResults``. For plain
    ``STATUS_RESPONSE`` / ``COMMAND_RESPONSE`` frames, decodes the Response
    dataclass directly — the firmware emits exactly the fields declared in
    the interface yaml, with no HoiResult trailer. HoiResult only rides on
    warning (prefix) or exception (separate payload, handled in
    ``send_command``) frames.

    Fatal (non-success, non-warning) entries from a warning frame surface
    through ``fatal_entries_by_channel`` and are lifted into a
    ``ChannelizedError`` by the backend — this decoder stays pure.
    """
    eff, _prefix = self._strip_warning_prefix(response)
    return interpret_hoi_success_payload(self, eff)

  def fatal_entries_by_channel(self, response: CommandResponse) -> dict[int, HcResultEntry]:
    """Return fatal entries keyed by 0-indexed PLR channel.

    Only non-success, non-warning entries from a warning-frame prefix are
    included; warnings remain log-only. Exception frames are handled
    separately in ``send_command`` via :func:`~pylabrobot.hamilton.tcp.hoi_error.parse_hamilton_error_entry`.

    ``entry_index`` passed to ``_channel_index_for_entry`` is the position of
    the entry in the *original* entries list (i.e. active-channel ordinal),
    not among fatal entries only — so bitmask / struct-array overrides can
    map ordinal → channel correctly even when earlier channels warned.
    """
    _eff, prefix_entries = self._strip_warning_prefix(response)
    per_channel: dict[int, HcResultEntry] = {}
    for i, entry in enumerate(prefix_entries):
      if entry.is_success:
        continue
      ch = self._channel_index_for_entry(i, entry)
      if ch is None:
        continue
      per_channel[ch] = entry
    return per_channel

  def _strip_warning_prefix(self, response: CommandResponse) -> tuple[bytes, list[HcResultEntry]]:
    """Strip the warning-frame HoiResult prefix, if present. Logs entries."""
    raw = response.hoi.params
    eff, prefix_entries = split_hoi_params_after_warning_prefix(response.hoi.action_code, raw)
    log_hoi_result_entries(type(self).__name__, prefix_entries, source="HOI prefix")
    return eff, prefix_entries

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


def hamilton_error_for_entry(entry: HcResultEntry, description: str) -> Exception:
  """Wrap an ``HcResultEntry`` in a ``RuntimeError`` using a pre-resolved description.

  ``description`` is sourced from the device itself via Interface 0 method 5
  (``EnumInfo``) — see ``HamiltonTCPClient._describe_entry``. The returned
  exception has ``.entry`` attached so callers can dispatch on
  ``entry.result`` / ``entry.interface_id`` / ``entry.address``.
  """
  err = RuntimeError(
    f"{description} (HcResult=0x{entry.result:04X}) "
    f"at {entry.address} iface={entry.interface_id} action={entry.action_id}"
  )
  err.entry = entry  # type: ignore[attr-defined]
  return err
