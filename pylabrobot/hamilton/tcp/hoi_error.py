"""HOI exception handling for Hamilton TCP.

Provides :class:`HoiError` for non-channel ``STATUS_EXCEPTION`` / ``COMMAND_EXCEPTION``
frames, and parsers that turn HOI exception/warning params into
:class:`~pylabrobot.hamilton.tcp.wire_types.HcResultEntry` rows and human-readable
strings. STATUS/COMMAND exception param walking and semicolon-separated HC-result
strings (warning-prefix fragment 1) live here; framing and success response decode
remain in :mod:`pylabrobot.hamilton.tcp.messages`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pylabrobot.hamilton.tcp.wire_types import (
  HamiltonDataType,
  HcResultEntry,
  decode_fragment,
)

_ERROR_ENTRY_RE: Optional[re.Pattern[str]] = None


def _error_entry_pattern() -> re.Pattern[str]:
  global _ERROR_ENTRY_RE
  if _ERROR_ENTRY_RE is None:
    _ERROR_ENTRY_RE = re.compile(
      r"0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)"
      r":0x([0-9a-fA-F]+),0x([0-9a-fA-F]+)(?:,0x([0-9a-fA-F]+))?"
    )
  return _ERROR_ENTRY_RE


def parse_hamilton_error_entries(params: bytes) -> List[HcResultEntry]:
  """Extract every ``HcResultEntry`` from HOI exception params.

  Hamilton ``COMMAND_EXCEPTION`` / ``STATUS_EXCEPTION`` responses can carry
  one ``HcResultEntry`` per affected channel, serialized as STRING fragments
  of the form ``0xMMMM.0xNNNN.0xOOOO:0xII,0xCCCC,0xRRRR`` (address,
  interface_id, method_id, hc_result). On a two-channel tip-pickup where both
  channels fail, the firmware emits two such strings — returning only the
  first one (as the old ``parse_hamilton_error_entry`` did) silently dropped
  the second channel's error.

  This walks every fragment and uses ``re.finditer`` within each STRING so
  multi-entry fragments are also covered. Returns entries in wire order — the
  backend uses ``_channel_index_for_entry(i, entry)`` on each to map to a PLR
  channel, matching the warning-frame prefix's ordinal semantics.
  """
  pat = _error_entry_pattern()
  out: List[HcResultEntry] = []
  if not params:
    return out
  offset = 0
  while offset + 4 <= len(params):
    type_id = params[offset]
    length = int.from_bytes(params[offset + 2 : offset + 4], "little")
    payload_end = offset + 4 + length
    if payload_end > len(params):
      return out
    data = params[offset + 4 : payload_end]
    if type_id == HamiltonDataType.STRING:
      text = data.decode("utf-8", errors="replace").rstrip("\x00").strip()
      for m in pat.finditer(text):
        out.append(
          HcResultEntry(
            module_id=int(m.group(1), 16),
            node_id=int(m.group(2), 16),
            object_id=int(m.group(3), 16),
            interface_id=int(m.group(4), 16),
            action_id=int(m.group(5), 16),
            result=int(m.group(6), 16) if m.group(6) else 0,
          )
        )
    offset = payload_end
  return out


def parse_hamilton_error_entry(params: bytes) -> Optional[HcResultEntry]:
  """Back-compat shim: returns the first entry from :func:`parse_hamilton_error_entries`."""
  entries = parse_hamilton_error_entries(params)
  return entries[0] if entries else None


def parse_hamilton_error_params(params: bytes) -> str:
  """Extract a human-readable message from HOI exception params.

  Hamilton COMMAND_EXCEPTION / STATUS_EXCEPTION responses send params as a
  sequence of DataFragments. Often the first or second fragment is a STRING
  (type_id=15) with a message like "0xE001.0x0001.0x1100:0x01,0x009,0x020A".
  This walks the fragment stream, decodes all fragments, and returns a
  single string (so you can see error codes and the message). If parsing
  fails, returns a safe fallback (hex or generic message).
  """
  parts = _parse_hamilton_error_fragments(params)
  if not parts:
    return params.hex() if params else "(empty)"
  return "; ".join(parts)


def _parse_hamilton_error_fragments(params: bytes) -> List[str]:
  """Decode all DataFragments in exception params. Returns list of "type: value" strings."""
  if not params:
    return []
  out: List[str] = []
  offset = 0
  while offset + 4 <= len(params):
    type_id = params[offset]
    length = int.from_bytes(params[offset + 2 : offset + 4], "little")
    payload_end = offset + 4 + length
    if payload_end > len(params):
      break
    data = params[offset + 4 : payload_end]
    try:
      decoded = decode_fragment(type_id, data)
      try:
        type_name = HamiltonDataType(type_id).name
      except ValueError:
        type_name = f"type_{type_id}"
      if isinstance(decoded, bytes):
        decoded = decoded.decode("utf-8", errors="replace").rstrip("\x00").strip()
      elif (
        type_id == HamiltonDataType.U8_ARRAY
        and isinstance(decoded, list)
        and all(isinstance(x, int) and 0 <= x <= 255 for x in decoded)
      ):
        b = bytes(decoded)
        s = b.decode("utf-8", errors="replace").rstrip("\x00").strip()
        # Strip leading control characters (e.g. length or flags before message text)
        s = s.lstrip(
          "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
        ).strip()
        if s and any(c.isprintable() or c.isspace() for c in s):
          decoded = s
      out.append(f"{type_name}={decoded}")
    except Exception:
      out.append(f"type_{type_id}=<{length} bytes>")
    offset = payload_end
  return out


def parse_hc_results_from_semicolon_string(text: str) -> list[HcResultEntry]:
  """Parse the semicolon-separated HOI result string (e.g. warning-prefix fragment 1).

  Same segment format as ``HoiDecoder2.GetHcResults`` in the vendor stack.
  Each segment is ``0xMMMM.0xMMMM.0xMMMM:0xII,0xAAAA,0xRRRR`` (address + iface, action, result).
  Malformed segments are skipped, matching the C# try/except behavior.
  """
  entries: list[HcResultEntry] = []
  for segment in text.split(";"):
    segment = segment.strip()
    if not segment:
      continue
    try:
      addr_part, rest = segment.split(":", 1)
      addr_part = addr_part.replace("0x", "").replace("0X", "")
      rest = rest.replace("0x", "").replace("0X", "")
      mod_s, node_s, obj_s = addr_part.split(".", 2)
      module_id = int(mod_s, 16)
      node_id = int(node_s, 16)
      object_id = int(obj_s, 16)
      fields = [x.strip() for x in rest.split(",")]
      if len(fields) < 3:
        continue
      interface_id = int(fields[0], 16)
      action_id = int(fields[1], 16)
      result = int(fields[2], 16)
      entries.append(
        HcResultEntry(
          module_id=module_id,
          node_id=node_id,
          object_id=object_id,
          interface_id=interface_id,
          action_id=action_id,
          result=result,
        )
      )
    except (ValueError, IndexError):
      continue
  return entries


class HoiError(Exception):
  """Raised for ``STATUS_EXCEPTION`` / ``COMMAND_EXCEPTION`` when the command wire shape
  does not carry per-channel parameters (e.g. void MLPrep queries).

  Wraps the same enriched per-entry exceptions as the channelized path
  (``describe_entry`` / error tables); :attr:`exceptions` is keyed by **wire entry
  index**, not physical channel index. Use :attr:`entries` for raw
  :class:`HcResultEntry` data.
  """

  def __init__(
    self,
    *,
    exceptions: Dict[int, Exception],
    entries: List[HcResultEntry],
    raw_response: bytes,
  ) -> None:
    self.exceptions = exceptions
    self.entries = entries
    self.raw_response = raw_response
    super().__init__(self._format_message())

  def _format_message(self) -> str:
    parts = [f"entry[{i}]: {self.exceptions[i]}" for i in sorted(self.exceptions)]
    return "HoiError(" + "; ".join(parts) + ")"
