"""Framing and protocol message layer for Hamilton TCP.

HoiParams is a fragment accumulator with add(value, wire_type) and
from_struct(obj); it has no type-specific encoding logic and delegates all
encoding to WireType.encode_into in wire_types. HoiParamsParser is a thin
cursor over sequential DataFragments; it reads [type_id:1][flags:1][length:2]
[data:N] headers and delegates value decoding to wire_types.decode_fragment().
parse_into_struct() is the dataclass codec that uses WireType annotations to
decode fragment sequences into typed instances.

Also: message builders (CommandMessage, InitMessage, RegistrationMessage) and
response parsers (CommandResponse, InitResponse, RegistrationResponse).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import fields as dc_fields
from typing import Any, List, Optional, cast, get_args, get_origin, get_type_hints

from pylabrobot.io.binary import Reader, Writer
from pylabrobot.hamilton.tcp.packets import (
  Address,
  HarpPacket,
  HoiPacket,
  IpPacket,
  RegistrationPacket,
)
from pylabrobot.hamilton.tcp.protocol import (
  HarpTransportableProtocol,
  Hoi2Action,
  RegistrationOptionType,
)
from pylabrobot.hamilton.tcp.wire_types import (
  HamiltonDataType,
  HcResultEntry,
  decode_fragment,
)

PADDED_FLAG = 0x01

logger = logging.getLogger(__name__)

# ============================================================================
# HOI PARAMETER ENCODING - DataFragment wrapping for HOI protocol
# ============================================================================
#
# Note: This is conceptually a separate layer in the Hamilton protocol
# architecture, but implemented here for efficiency since it's exclusively
# used by HOI messages (CommandMessage).
# ============================================================================


class HoiParams:
  """Builder for HOI parameters with automatic DataFragment wrapping.

  Each parameter is wrapped with DataFragment header before being added:
  [type_id:1][flags:1][length:2][data:n]

  This ensures HOI parameters are always correctly formatted and eliminates
  the possibility of forgetting to add DataFragment headers.

  Example:
    Creates concatenated DataFragments:
    [0x03|0x00|0x04|0x00|100][0x0F|0x00|0x05|0x00|"test\0"][0x1C|0x00|...array...]

    params = (HoiParams()
              .add(100, I32)
              .add("test", Str)
              .add([1, 2, 3], U32Array)
              .build())
  """

  def __init__(self):
    self._fragments: list[bytes] = []

  def _add_fragment(self, type_id: int, data: bytes, flags: int = 0) -> "HoiParams":
    """Add a DataFragment with the given type_id and data.

    Creates: [type_id:1][flags:1][length:2][data:n]

    When flags & PADDED_FLAG, appends a trailing pad byte (Prep convention).
    Callers pass unpadded data; _add_fragment centralizes pad handling.

    Args:
      type_id: Data type ID
      data: Fragment data bytes (unpadded; pad added here when flags set)
      flags: Fragment flags (default: 0; PADDED_FLAG for BoolArray, PaddedBool, PaddedU8)
    """
    if flags & PADDED_FLAG:
      data = data + b"\x00"
    fragment = Writer().u8(type_id).u8(flags).u16(len(data)).raw_bytes(data).finish()
    self._fragments.append(fragment)
    return self

  def add(self, value: Any, wire_type: Any) -> "HoiParams":
    """Encode a value using its WireType and append the DataFragment.

    wire_type may be a WireType instance or an Annotated alias (e.g. I32, Str).
    """
    if hasattr(wire_type, "__metadata__"):
      wire_type = wire_type.__metadata__[0]
    return cast("HoiParams", wire_type.encode_into(value, self))

  # ------------------------------------------------------------------
  # Generic dataclass serialiser (wire_types.py Annotated metadata)
  # ------------------------------------------------------------------

  @classmethod
  def from_struct(cls, obj) -> "HoiParams":
    """Serialize any dataclass whose fields use ``Annotated`` wire-type metadata.

    Fields without ``Annotated`` metadata (e.g. plain ``Address``) are skipped.
    The polymorphic ``WireType.encode_into`` on each annotation handles all
    dispatch -- no if/elif required here.
    """
    from dataclasses import fields as dc_fields
    from typing import get_type_hints

    from pylabrobot.hamilton.tcp.wire_types import WireType

    hints = get_type_hints(type(obj), include_extras=True)
    params = cls()
    for f in dc_fields(obj):
      ann = hints.get(f.name)
      if ann is None or not hasattr(ann, "__metadata__"):
        continue
      meta = ann.__metadata__[0]
      if not isinstance(meta, WireType):
        continue
      params = meta.encode_into(getattr(obj, f.name), params)
    return cast("HoiParams", params)

  def build(self) -> bytes:
    """Return concatenated DataFragments."""
    return b"".join(self._fragments)

  def count(self) -> int:
    """Return number of fragments (parameters)."""
    return len(self._fragments)


class HoiParamsParser:
  """Cursor over sequential DataFragments in an HOI payload.

  Reads [type_id:1][flags:1][length:2][data:N] headers and delegates
  value decoding to the unified codec in wire_types.decode_fragment().
  """

  def __init__(self, data: bytes):
    if not isinstance(data, bytes):
      raise TypeError(
        f"HoiParamsParser requires bytes, got {type(data).__name__}. "
        "Use get_structs_raw() and inspect_hoi_params() to see the wire format."
      )
    self._data = data
    self._offset = 0

  def parse_next(self) -> tuple[int, Any]:
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Insufficient data at offset {self._offset}")
    type_id = self._data[self._offset]
    flags = self._data[self._offset + 1]
    length = int.from_bytes(self._data[self._offset + 2 : self._offset + 4], "little")
    payload_end = self._offset + 4 + length
    if payload_end > len(self._data):
      raise ValueError(
        f"DataFragment data extends beyond buffer: need {payload_end}, have {len(self._data)}"
      )
    data = self._data[self._offset + 4 : payload_end]
    self._offset = payload_end
    if (flags & PADDED_FLAG) and len(data) > 0:
      data = data[:-1]
    return type_id, decode_fragment(type_id, data)

  def parse_next_raw(self) -> tuple[int, int, int, bytes]:
    """Return (type_id, flags, length, payload_bytes) without decoding.

    Use when the wire declares STRING (type_id=15) but the payload is binary
    (e.g. GetMethod parameter_types). Normal parse_next() would UTF-8 decode
    and fail on bytes like 0xaa.
    """
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Insufficient data at offset {self._offset}")
    type_id = self._data[self._offset]
    flags = self._data[self._offset + 1]
    length = int.from_bytes(self._data[self._offset + 2 : self._offset + 4], "little")
    payload_end = self._offset + 4 + length
    if payload_end > len(self._data):
      raise ValueError(
        f"DataFragment data extends beyond buffer: need {payload_end}, have {len(self._data)}"
      )
    payload = self._data[self._offset + 4 : payload_end]
    self._offset = payload_end
    return type_id, flags, length, payload

  def has_remaining(self) -> bool:
    return self._offset < len(self._data)

  def remaining(self) -> bytes:
    """Unconsumed payload bytes (from current cursor to end)."""
    return self._data[self._offset :]

  def skip_next(self) -> None:
    """Advance past one DataFragment without decoding the payload."""
    if self._offset + 4 > len(self._data):
      raise ValueError(f"Insufficient data at offset {self._offset}")
    length = int.from_bytes(self._data[self._offset + 2 : self._offset + 4], "little")
    payload_end = self._offset + 4 + length
    if payload_end > len(self._data):
      raise ValueError(
        f"DataFragment data extends beyond buffer: need {payload_end}, have {len(self._data)}"
      )
    self._offset = payload_end

  def parse_all(self) -> list[tuple[int, Any]]:
    results = []
    while self.has_remaining():
      results.append(self.parse_next())
    return results


def inspect_hoi_params(params: bytes) -> List[dict]:
  """Inspect raw HOI params bytes fragment-by-fragment for debugging.

  Walks the DataFragment stream [type_id:1][flags:1][length:2][data:N] and
  returns a list of dicts with: type_id, flags, length, payload_hex (first 80
  chars), payload_len, decoded (decode_fragment result or exception message).
  Use this to see exactly what the device sends and fix response parsing.

  Example:
    raw, fragments = await intro.get_structs_raw(mph_addr, 1)
    for i, f in enumerate(fragments):
      print(f\"{i}: type_id={f['type_id']} len={f['length']} decoded={f['decoded']!r}\")
  """
  if not params:
    return []
  out: List[dict] = []
  offset = 0
  while offset + 4 <= len(params):
    type_id = params[offset]
    flags = params[offset + 1]
    length = int.from_bytes(params[offset + 2 : offset + 4], "little")
    payload_end = offset + 4 + length
    if payload_end > len(params):
      out.append(
        {
          "type_id": type_id,
          "flags": flags,
          "length": length,
          "payload_hex": "<incomplete>",
          "payload_len": 0,
          "decoded": f"<buffer end: need {payload_end}, have {len(params)}>",
        }
      )
      break
    data = params[offset + 4 : payload_end]
    hex_preview = data.hex() if len(data) <= 40 else data[:40].hex() + "..."
    try:
      decoded = decode_fragment(type_id, data)
      if isinstance(decoded, bytes):
        decoded = (
          decoded.decode("utf-8", errors="replace").rstrip("\x00") or f"<bytes {len(decoded)}>"
        )
      decoded_repr = (
        repr(decoded) if not isinstance(decoded, (str, int, float, bool)) else str(decoded)
      )
      if isinstance(decoded, list):
        decoded_repr = (
          f"list[len={len(decoded)}](elem0_type={type(decoded[0]).__name__ if decoded else 'n/a'})"
        )
    except Exception as e:
      decoded_repr = f"<decode error: {e!r}>"
    out.append(
      {
        "type_id": type_id,
        "flags": flags,
        "length": length,
        "payload_hex": hex_preview,
        "payload_len": len(data),
        "decoded": decoded_repr,
      }
    )
    offset = payload_end
  return out


_ERROR_ENTRY_RE = None  # lazy-compiled below


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
  import re

  global _ERROR_ENTRY_RE
  if _ERROR_ENTRY_RE is None:
    _ERROR_ENTRY_RE = re.compile(
      r"0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)"
      r":0x([0-9a-fA-F]+),0x([0-9a-fA-F]+)(?:,0x([0-9a-fA-F]+))?"
    )

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
      for m in _ERROR_ENTRY_RE.finditer(text):
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


def _parse_get_hc_results_string(text: str) -> list[HcResultEntry]:
  """Parse the semicolon-separated warning string from ``HoiDecoder2.GetHcResults`` (fragment 1).

  Each segment is ``0xMMMM.0xMMMM.0xMMMM:0xII,0xAAAA,0xRRRR`` (HarpAddress.ToString + iface, action, result).
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


def hoi_action_code_base(action_byte: int) -> int:
  """Lower 4 bits of HOI action field (response-required bit is 0x10)."""
  return action_byte & 0x0F


def split_hoi_params_after_warning_prefix(
  action_code: int, params: bytes
) -> tuple[bytes, list[HcResultEntry]]:
  """If action is StatusWarning/CommandWarning, drop the first two fragments and parse the string aggregate.

  Mirrors ``SystemController.SendAndReceive``: out-parameters start at fragment index 2; fragment 1 holds
  the formatted warning list consumed by ``HoiResult(HoiPacket2)`` / ``GetHcResults``.
  """
  if not params:
    return params, []
  base = hoi_action_code_base(action_code)
  if base not in (Hoi2Action.STATUS_WARNING, Hoi2Action.COMMAND_WARNING):
    return params, []

  parser = HoiParamsParser(params)
  if not parser.has_remaining():
    return params, []
  try:
    _tid0, _v0 = parser.parse_next()
    if not parser.has_remaining():
      return params, []
    _tid1, v1 = parser.parse_next()
  except ValueError:
    return params, []

  rest = parser.remaining()
  prefix_entries: list[HcResultEntry] = []
  if isinstance(v1, str):
    prefix_entries = _parse_get_hc_results_string(v1)
  elif isinstance(v1, (bytes, bytearray)):
    prefix_entries = _parse_get_hc_results_string(bytes(v1).decode("utf-8", errors="replace"))
  return rest, prefix_entries


def log_hoi_result_entries(command_name: str, entries: list[HcResultEntry], *, source: str) -> None:
  """Log non-success ``HcResultEntry`` rows (0x0000 skipped)."""
  for entry in entries:
    if entry.result == 0:
      continue
    logger.warning(
      "%s %s channel result at %d:%d:%d iface=%d action=%d: 0x%04X (%s)",
      command_name,
      source,
      entry.module_id,
      entry.node_id,
      entry.object_id,
      entry.interface_id,
      entry.action_id,
      entry.result,
      "warning" if entry.is_warning else "error",
    )


def interpret_hoi_success_payload(command: Any, params_bytes: bytes) -> Any:
  """Decode command ``Response`` from HOI params.

  Used for CommandResponse / StatusResponse payloads after exception and
  warning-prefix handling. Success frames carry only the fields declared in
  the Response dataclass — no HoiResult trailer (see firmware yaml dumps and
  protocol decoder behavior; HoiResult only rides on warning-prefix or exception
  frames).
  """
  cls = type(command)
  if not params_bytes:
    return None

  if hasattr(cls, "Response"):
    return parse_into_struct(HoiParamsParser(params_bytes), cls.Response)

  return command.parse_response_parameters(params_bytes)


def parse_into_struct(parser: HoiParamsParser, cls: type) -> Any:
  """Decode a sequence of DataFragments into a dataclass instance using its wire-type annotations.

  Mirrors HoiParams.from_struct: walks the same Annotated field metadata and, for each field in
  order, consumes one fragment (via parser.parse_next()). Scalars/arrays/string yield the value
  as returned by the parser; Struct recurses on the payload bytes; StructArray yields a list of
  recursively decoded instances.

  Args:
    parser: Parser positioned at the start of the fragment sequence (e.g. response payload).
    cls: Dataclass type whose fields are annotated with wire_types (F32, Struct(), etc.).

  Returns:
    An instance of cls with fields populated from the parsed fragments.

  Raises:
    ValueError: If data is malformed or insufficient.
  """
  from pylabrobot.hamilton.tcp.wire_types import (
    CountedFlatArray,
    Struct,
    StructArray,
    WireType,
  )

  hints = get_type_hints(cls, include_extras=True)
  values: dict[str, Any] = {}
  for f in dc_fields(cls):
    ann = hints.get(f.name)
    if ann is None or not hasattr(ann, "__metadata__"):
      continue
    meta = ann.__metadata__[0]
    if not isinstance(meta, WireType):
      continue

    if isinstance(meta, CountedFlatArray):
      _, raw = parser.parse_next()
      element_type = get_args(get_args(ann)[0])[0]
      if isinstance(raw, list):
        # Single fragment was STRUCTURE_ARRAY: list of payload bytes per element
        if raw and not isinstance(raw[0], bytes):
          raise ValueError(
            f"CountedFlatArray decoded to list of {type(raw[0]).__name__}, expected "
            "list of bytes (STRUCTURE_ARRAY). Use get_structs_raw() and "
            "inspect_hoi_params() to see the exact wire format."
          )
        values[f.name] = [parse_into_struct(HoiParamsParser(p), element_type) for p in raw]
      else:
        # Count then N flat fragments (count-prefixed stream)
        count = int(raw)
        values[f.name] = [parse_into_struct(parser, element_type) for _ in range(count)]
      continue

    type_id, value = parser.parse_next()

    if isinstance(meta, Struct):
      inner_type = get_args(ann)[0]
      value = parse_into_struct(HoiParamsParser(value), inner_type)
    elif isinstance(meta, StructArray):
      inner_ann = get_args(ann)[0]
      if get_origin(inner_ann) is list:
        element_type = get_args(inner_ann)[0]
      else:
        element_type = inner_ann
      value = [parse_into_struct(HoiParamsParser(p), element_type) for p in value]
    # else: decode_fragment() already returned correctly-typed value

    values[f.name] = value

  return cls(**values)


# ============================================================================
# MESSAGE BUILDERS
# ============================================================================


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
    params: HoiParams,
    action_code: int = 3,  # Default: COMMAND_REQUEST
    harp_protocol: int = 2,  # Default: HOI2
    ip_protocol: int = 6,  # Default: OBJECT_DISCOVERY
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
    self.params = params
    self.action_code = action_code
    self.harp_protocol = harp_protocol
    self.ip_protocol = ip_protocol

  def build(
    self,
    src: Address,
    seq: int,
    harp_response_required: bool = True,
    hoi_response_required: bool = False,
  ) -> bytes:
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
      response_required=hoi_response_required,
    )

    # Build HARP - it handles its own action byte construction
    harp = HarpPacket(
      src=src,
      dst=self.dest,
      seq=seq,
      protocol=self.harp_protocol,
      action_code=self.action_code,
      payload=hoi.pack(),
      response_required=harp_response_required,
    )

    # Wrap in IP packet
    ip = IpPacket(protocol=self.ip_protocol, payload=harp.pack())

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
    ip_protocol: int = 6,  # Default: OBJECT_DISCOVERY
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
    self, option_type: RegistrationOptionType, protocol: int = 2, request_id: int = 1
  ) -> "RegistrationMessage":
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
    data = Writer().u8(protocol).u8(request_id).finish()
    option = Writer().u8(option_type).u8(len(data)).raw_bytes(data).finish()
    self.options.extend(option)
    return self

  def build(
    self,
    src: Address,
    req_addr: Address,
    res_addr: Address,
    seq: int,
    harp_action_code: int = 3,  # Default: COMMAND_REQUEST
    harp_response_required: bool = True,  # Default: request with response
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
      options=bytes(self.options),
    )

    # Wrap in HARP packet
    harp = HarpPacket(
      src=src,
      dst=self.dest,
      seq=seq,
      protocol=self.harp_protocol,
      action_code=harp_action_code,
      payload=reg.pack(),
      response_required=harp_response_required,
    )

    # Wrap in IP packet
    ip = IpPacket(protocol=self.ip_protocol, payload=harp.pack())

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
    ip_protocol: int = 7,  # Default: INITIALIZATION
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
    params = (
      Writer()
      # Frame
      .u8(0)  # version
      .u8(0)  # message_id
      .u8(3)  # count (3 parameters)
      .u8(0)  # unknown
      # Parameter 1: connection_id (request allocation)
      .u8(1)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(0)  # value (0 = request allocation)
      # Parameter 2: connection_type
      .u8(2)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(self.connection_type)  # value
      # Parameter 3: timeout
      .u8(4)  # param id
      .u8(16)  # param type
      .u16(0)  # reserved
      .u16(self.timeout)  # value
      .finish()
    )

    # Build IP packet
    packet_size = 1 + 1 + 2 + len(params)  # protocol + version + opts_len + params

    return (
      Writer()
      .u16(packet_size)
      .u8(self.ip_protocol)
      .u8(self.protocol_version)
      .u16(0)  # options_length
      .raw_bytes(params)
      .finish()
    )


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
  def from_bytes(cls, data: bytes) -> "InitResponse":
    """Parse initialization response.

    Args:
      data: Raw bytes from TCP socket

    Returns:
      Parsed InitResponse with connection parameters
    """
    # Skip IP header (size + protocol + version + opts_len = 6 bytes)
    parser = Reader(data[6:])

    # Parse frame
    _version = parser.u8()  # Read but unused
    _message_id = parser.u8()  # Read but unused
    _count = parser.u8()  # Read but unused
    _unknown = parser.u8()  # Read but unused

    # Parse parameter 1 (client_id)
    _param1_id = parser.u8()  # Read but unused
    _param1_type = parser.u8()  # Read but unused
    _param1_reserved = parser.u16()  # Read but unused
    client_id = parser.u16()

    # Parse parameter 2 (connection_type)
    _param2_id = parser.u8()  # Read but unused
    _param2_type = parser.u8()  # Read but unused
    _param2_reserved = parser.u16()  # Read but unused
    connection_type = parser.u16()

    # Parse parameter 4 (timeout)
    _param4_id = parser.u8()  # Read but unused
    _param4_type = parser.u8()  # Read but unused
    _param4_reserved = parser.u16()  # Read but unused
    timeout = parser.u16()

    return cls(
      raw_bytes=data, client_id=client_id, connection_type=connection_type, timeout=timeout
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
  def from_bytes(cls, data: bytes) -> "RegistrationResponse":
    """Parse registration response.

    Args:
      data: Raw bytes from TCP socket

    Returns:
      Parsed RegistrationResponse with all layers
    """
    ip = IpPacket.unpack(data)
    harp = HarpPacket.unpack(ip.payload)
    registration = RegistrationPacket.unpack(harp.payload)

    return cls(raw_bytes=data, ip=ip, harp=harp, registration=registration)

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
  def from_bytes(cls, data: bytes) -> "CommandResponse":
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

    return cls(raw_bytes=data, ip=ip, harp=harp, hoi=hoi)

  @property
  def sequence_number(self) -> int:
    """Get sequence number from HARP layer."""
    return self.harp.seq
