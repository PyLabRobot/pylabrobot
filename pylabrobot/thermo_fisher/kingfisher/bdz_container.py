"""Lossless, bounded decoding of BindIt BDZ containers for KingFisher instruments."""

import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass

MAX_XML_SIZE = 16 * 1024 * 1024


@dataclass(frozen=True)
class BdzContainer:
  """Original XML payloads and container metadata, independent of instrument semantics."""

  version: str
  name: str
  parameters_type: int
  properties: bytes
  exported: bytes


def parse_xml(payload: bytes, root_name: str) -> ET.Element:
  """Parse bounded XML without declarations that can expand external data."""
  if len(payload) > MAX_XML_SIZE:
    raise ValueError("BindIt XML exceeds the inspection size limit")
  # BindIt exports are UTF-8; reject alternate encodings rather than bypass this check.
  try:
    text = payload.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper() or "\x00" in text:
      raise ValueError("Unsupported XML declaration or encoding")
    root = ET.fromstring(text)
  except (UnicodeError, ET.ParseError) as error:
    raise ValueError("Invalid BindIt XML") from error
  if root.tag != root_name:
    raise ValueError(f"Expected {root_name}, got {root.tag}")
  return root


def _string(data: bytes, offset: int) -> tuple[str, int]:
  """Read a length-prefixed UTF-8 container string with bounds checking."""
  if offset + 2 > len(data):
    raise ValueError("Truncated BindIt string length")
  size = struct.unpack_from("<H", data, offset)[0]
  end = offset + 2 + size
  if end > len(data):
    raise ValueError("Truncated BindIt string")
  try:
    return data[offset + 2 : end].decode("utf-8"), end
  except UnicodeError as error:
    raise ValueError("Invalid BindIt string") from error


def _gzip(data: bytes, offset: int, size: int) -> tuple[bytes, int]:
  """Validate a complete size-bounded gzip member, including CRC and EOF."""
  end = offset + size
  if size <= 0 or end > len(data):
    raise ValueError("Invalid compressed stream size")
  decoder = zlib.decompressobj(31)
  try:
    payload = decoder.decompress(data[offset:end], MAX_XML_SIZE + 1)
  except zlib.error as error:
    raise ValueError("Corrupt BindIt gzip member") from error
  if len(payload) > MAX_XML_SIZE:
    raise ValueError("BindIt XML exceeds the inspection size limit")
  if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
    raise ValueError("BindIt gzip member does not match its declared size")
  return payload, end


def read_container(data: bytes) -> BdzContainer:
  """Parse variable-length header fields and validate both members and trailer."""
  if len(data) > 2 * MAX_XML_SIZE:
    raise ValueError("BindIt file exceeds the inspection size limit")
  if len(data) < 16 or data[:12] != bytes.fromhex("b6751cf201000a0001000000"):
    raise ValueError("Unsupported BindIt header")
  strings_size = struct.unpack_from("<I", data, 12)[0]
  vendor, offset = _string(data, 16)
  version, offset = _string(data, offset)
  if vendor != "BindIt Software" or offset - 16 != strings_size:
    raise ValueError("Invalid BindIt vendor/version header")
  if offset + 18 > len(data):
    raise ValueError("Truncated BindIt header")
  if data[offset : offset + 6] != bytes.fromhex("0b0001000000"):
    raise ValueError("Unsupported BindIt object header")
  content_size = struct.unpack_from("<I", data, offset + 6)[0]
  if content_size != len(data) - (offset + 20):
    raise ValueError("BindIt file size does not match its header")
  if data[offset + 10 : offset + 14] != bytes.fromhex("00000001"):
    raise ValueError("Invalid first stream marker")
  size = struct.unpack_from("<I", data, offset + 14)[0]
  props, offset = _gzip(data, offset + 18, size)
  if data[offset : offset + 4] != bytes.fromhex("01000001") or offset + 8 > len(data):
    raise ValueError("Invalid second stream marker")
  size = struct.unpack_from("<I", data, offset + 4)[0]
  exported, offset = _gzip(data, offset + 8, size)
  trailer = data[offset:]
  if len(trailer) < 42 or struct.unpack_from("<I", trailer)[0] != 2:
    raise ValueError("Invalid BindIt trailer")
  parameters_type = struct.unpack_from("<I", trailer, 8)[0]
  if trailer[12:30] != b"\x00" * 14 + b"\x01\x00\x00\x00":
    raise ValueError("Unsupported BindIt trailer fields")
  name, end = _string(trailer, 30)
  if struct.unpack_from("<I", trailer, 4)[0] != end - 32 + 24:
    raise ValueError("Invalid BindIt trailer name size")
  if trailer[end:] != bytes.fromhex("01000100000000000000"):
    raise ValueError("Invalid BindIt trailing bytes")
  return BdzContainer(version, name, parameters_type, props, exported)
