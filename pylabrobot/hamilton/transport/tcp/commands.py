"""Immutable Hamilton requests with explicit wire encoding and typed response decoding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Generic, Optional, TypeVar

from pylabrobot.hamilton.transport.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  log_hoi_result_entries,
  split_hoi_params_after_warning_prefix,
)
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry

ResponseT = TypeVar("ResponseT", covariant=True)


@dataclass(frozen=True)
class TCPCommand(Generic[ResponseT]):
  """A reusable request, independent of connection identity and sequence allocation.

  Define subclasses as frozen dataclasses. Encode parameters explicitly in
  ``build_parameters`` and decode the success payload in ``parse_response_parameters``.
  The action on the request determines whether it queries or changes device state.
  """

  dest: Address
  protocol: ClassVar[HamiltonProtocol] = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id: ClassVar[Optional[int]] = None
  command_id: ClassVar[Optional[int]] = None
  uses_physical_channels: ClassVar[bool] = False
  action_code: ClassVar[int] = 3
  harp_protocol: ClassVar[int] = 2
  ip_protocol: ClassVar[int] = 6

  def build_parameters(self) -> HoiParams:
    """Encode request parameters; the default request has no parameters."""
    return HoiParams()

  def build(self, src: Address, seq: int, response_required: bool = True) -> bytes:
    """Encode this request with the source and sequence supplied by its session."""
    if self.interface_id is None or self.command_id is None:
      raise ValueError(f"{type(self).__name__} must define interface_id and command_id")
    return CommandMessage(
      dest=self.dest,
      interface_id=self.interface_id,
      method_id=self.command_id,
      params=self.build_parameters(),
      action_code=self.action_code,
      harp_protocol=self.harp_protocol,
      ip_protocol=self.ip_protocol,
    ).build(src, seq, harp_response_required=response_required)

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map a ``HcResultEntry`` to a 0-indexed PLR channel, or ``None`` to skip.

    The default is the entry's position in the HoiResult. Commands with channel
    selections override this to map active-channel ordinals to PLR channels.
    """
    return entry_index

  def interpret_response(self, response: CommandResponse) -> ResponseT:
    """Pure decoder for a success response — never raises on channel errors.

    For ``STATUS_WARNING`` / ``COMMAND_WARNING`` frames, strips the leading
    summary + formatted-string prefix (per ``SystemController.SendAndReceive``)
    and logs entries parsed via ``HoiDecoder2.GetHcResults``. For plain
    ``STATUS_RESPONSE`` / ``COMMAND_RESPONSE`` frames, decodes the Response
    dataclass directly — the firmware emits exactly the fields declared in
    the interface yaml, with no HoiResult trailer. HoiResult only rides on
    warning (prefix) or exception (separate payload, handled in
    ``execute``) frames.

    The session checks fatal entries before calling the decoder.
    """
    eff, _prefix = self._strip_warning_prefix(response)
    return self.parse_response_parameters(eff)

  def fatal_entries_by_channel(self, response: CommandResponse) -> dict[int, HcResultEntry]:
    """Return fatal entries keyed by 0-indexed PLR channel.

    Only non-success, non-warning entries from a warning-frame prefix are
    included; warnings remain log-only. Exception frames are handled
    separately in ``execute`` via :func:`~pylabrobot.hamilton.transport.tcp.hoi_error.parse_hamilton_error_entry`.

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
  def parse_response_parameters(cls, data: bytes) -> ResponseT:
    """Decode success parameters; subclasses must declare their response type."""
    raise NotImplementedError(f"{cls.__name__} must implement parse_response_parameters")


class HoiEntryError(RuntimeError):
  """One firmware result with its original structured entry."""

  def __init__(self, entry: HcResultEntry, description: str):
    """Preserve the wire entry alongside its offline description."""
    self.entry = entry
    super().__init__(
      f"{description} (HcResult=0x{entry.result:04X}) "
      f"at {entry.address} iface={entry.interface_id} action={entry.action_id}"
    )


def hamilton_error_for_entry(entry: HcResultEntry, description: str) -> HoiEntryError:
  """Wrap a firmware entry without performing additional I/O."""
  return HoiEntryError(entry, description)
