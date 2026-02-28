"""Generic SiLA 2 / protobuf wire-format helpers (no grpc_tools dependency).

These utilities encode and decode raw protobuf messages and standard SiLA 2
wrapper types, making it possible to talk to any SiLA 2 server using only
the ``grpc`` channel API.
"""

import base64
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import grpc

# ---------------------------------------------------------------------------
# Protobuf wire-format encoding
# ---------------------------------------------------------------------------


def encode_varint(value: int) -> bytes:
  parts = bytearray()
  while value > 0x7F:
    parts.append((value & 0x7F) | 0x80)
    value >>= 7
  parts.append(value & 0x7F)
  return bytes(parts)


def encode_signed_varint(value: int) -> bytes:
  if value < 0:
    value = (1 << 64) + value
  return encode_varint(value)


def length_delimited(field_number: int, data: bytes) -> bytes:
  tag = encode_varint((field_number << 3) | 2)
  return tag + encode_varint(len(data)) + data


def varint_field(field_number: int, value: int) -> bytes:
  tag = encode_varint((field_number << 3) | 0)
  return tag + encode_signed_varint(value)


# ---------------------------------------------------------------------------
# Protobuf wire-format decoding
# ---------------------------------------------------------------------------

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_32BIT = 5


def decode_varint(data: bytes, pos: int) -> Tuple[int, int]:
  result = 0
  shift = 0
  while True:
    b = data[pos]
    result |= (b & 0x7F) << shift
    pos += 1
    if not (b & 0x80):
      break
    shift += 7
  return result, pos


def decode_fields(data: bytes) -> Dict[int, List[Tuple[int, Any]]]:
  fields: Dict[int, List[Tuple[int, Any]]] = defaultdict(list)
  pos = 0
  while pos < len(data):
    tag, pos = decode_varint(data, pos)
    field_number = tag >> 3
    wire_type = tag & 0x07
    if wire_type == WIRE_VARINT:
      val_int, pos = decode_varint(data, pos)
      fields[field_number].append((wire_type, val_int))
    elif wire_type == WIRE_LENGTH_DELIMITED:
      length, pos = decode_varint(data, pos)
      val_bytes = data[pos : pos + length]
      pos += length
      fields[field_number].append((wire_type, val_bytes))
    elif wire_type == WIRE_64BIT:
      val_bytes = data[pos : pos + 8]
      pos += 8
      fields[field_number].append((wire_type, val_bytes))
    elif wire_type == WIRE_32BIT:
      val_bytes = data[pos : pos + 4]
      pos += 4
      fields[field_number].append((wire_type, val_bytes))
    else:
      break
  return dict(fields)


def get_field_bytes(fields: Dict[int, List[Tuple[int, Any]]], field_number: int) -> Optional[bytes]:
  entries = fields.get(field_number, [])
  for wire_type, value in entries:
    if wire_type == WIRE_LENGTH_DELIMITED:
      return bytes(value)
  return None


def get_field_varint(fields: Dict[int, List[Tuple[int, Any]]], field_number: int) -> Optional[int]:
  entries = fields.get(field_number, [])
  for wire_type, value in entries:
    if wire_type == WIRE_VARINT:
      return int(value)
  return None


def varint_as_signed(value: int) -> int:
  if value > 0x7FFFFFFFFFFFFFFF:
    return value - (1 << 64)
  return value


def extract_proto_strings(data: bytes) -> List[str]:
  """Recursively extract all string-like fields from a protobuf message."""
  strings = []
  try:
    fields = decode_fields(data)
    for entries in fields.values():
      for wire_type, value in entries:
        if wire_type == WIRE_LENGTH_DELIMITED:
          try:
            s = value.decode("utf-8")
            if s.isprintable() and len(s) > 0:
              strings.append(s)
          except UnicodeDecodeError:
            pass
          strings.extend(extract_proto_strings(value))
  except Exception:
    pass
  return strings


# ---------------------------------------------------------------------------
# gRPC error decoding
# ---------------------------------------------------------------------------


def decode_grpc_error(error: grpc.RpcError) -> str:
  """Decode a SiLA gRPC error into a human-readable string.

  SiLA error details are base64-encoded protobuf in the gRPC details field.
  """
  details = error.details() if hasattr(error, "details") else str(error)
  if not details:
    return str(error)

  try:
    raw = base64.b64decode(details)
    strings = extract_proto_strings(raw)
    if strings:
      return ": ".join(strings)
  except Exception:
    pass

  return details


# ---------------------------------------------------------------------------
# SiLA 2 standard wrapper types
# ---------------------------------------------------------------------------


def sila_string(value: str) -> bytes:
  return length_delimited(1, value.encode("utf-8"))


def sila_integer(value: int) -> bytes:
  return varint_field(1, value)


# ---------------------------------------------------------------------------
# SiLA 2 standard message builders / decoders
# ---------------------------------------------------------------------------


def lock_server_params(lock_id: str, timeout_seconds: int = 60) -> bytes:
  return length_delimited(1, sila_string(lock_id)) + length_delimited(
    2, sila_integer(timeout_seconds)
  )


def unlock_server_params(lock_id: str) -> bytes:
  return length_delimited(1, sila_string(lock_id))


def metadata_lock_identifier(lock_id: str) -> bytes:
  return length_delimited(1, sila_string(lock_id))


def command_execution_uuid(uuid_str: str) -> bytes:
  return length_delimited(1, uuid_str.encode("utf-8"))


def decode_sila_string_response(data: bytes) -> str:
  """Decode a response containing a single SiLA String field (field 1)."""
  fields = decode_fields(data)
  sila_str_msg = get_field_bytes(fields, 1)
  if sila_str_msg is None:
    raise ValueError("No SiLA String in response")
  inner_fields = decode_fields(sila_str_msg)
  value = get_field_bytes(inner_fields, 1)
  if value is None:
    raise ValueError("No value in SiLA String")
  return value.decode("utf-8")


def decode_command_confirmation(data: bytes) -> str:
  return decode_sila_string_response(data)
