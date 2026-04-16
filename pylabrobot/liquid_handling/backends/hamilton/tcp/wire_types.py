"""Unified bidirectional codec layer for Hamilton DataFragments.

Single source of truth for DataFragment type IDs, encoding, and decoding. Each
WireType handles both encode (encode_into) and decode (decode_from) via the
same format; no separate dispatch tables or coercion blocks.

Layout: (1) HamiltonDataType enum (type IDs for [type_id:1][flags:1][length:2]
[data:N]); (2) WireType hierarchy and Annotated type aliases (I32, F32, Bool,
Str, I32Array, etc.); (3) type registry and decode_fragment(). HoiParams and
HoiParamsParser delegate to this layer exclusively.
"""

from __future__ import annotations

import struct as _struct
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
  from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams


# ---------------------------------------------------------------------------
# Hamilton DataFragment type IDs (codec layer)
# ---------------------------------------------------------------------------
# Type identifiers for the DataFragment wire format [type_id:1][flags:1][length:2][data:N].
# From Hamilton.Components.TransportLayer.Protocols.Parameter.ParameterTypes.


class HamiltonDataType(IntEnum):
  """Hamilton parameter data types for wire encoding in DataFragments."""

  VOID = 0
  # Scalar integer types
  I8 = 1
  I16 = 2
  I32 = 3
  U8 = 4
  U16 = 5
  U32 = 6
  I64 = 36
  U64 = 37

  # Floating-point types
  F32 = 40
  F64 = 41

  # String and boolean
  STRING = 15
  BOOL = 23

  # Structure and enum types (Prep and introspection)
  STRUCTURE = 30
  STRUCTURE_ARRAY = 31
  ENUM = 32
  HC_RESULT = 33  # Same wire format as U16, used for error codes
  ENUM_ARRAY = 35

  # Array types
  U8_ARRAY = 22
  I8_ARRAY = 24
  I16_ARRAY = 25
  U16_ARRAY = 26
  I32_ARRAY = 27
  U32_ARRAY = 28
  BOOL_ARRAY = 29
  STRING_ARRAY = 34
  I64_ARRAY = 38
  U64_ARRAY = 39
  F32_ARRAY = 42
  F64_ARRAY = 43


# ---------------------------------------------------------------------------
# WireType hierarchy
# ---------------------------------------------------------------------------


class WireType:
  """Base class: a wire-format type that can encode and decode HoiParams fragments."""

  __slots__ = ("type_id",)

  def __init__(self, type_id: int):
    self.type_id = type_id

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    raise NotImplementedError

  def decode_from(self, data: bytes) -> Any:
    raise NotImplementedError


class Scalar(WireType):
  """Fixed-size scalar encoded via ``struct.pack(fmt, value)``.

  When *padded* is ``True`` the Prep convention is used: flags byte = 0x01
  and one ``\\x00`` pad byte is appended after the value.
  """

  __slots__ = ("fmt", "padded")

  def __init__(self, type_id: int, fmt: str, padded: bool = False):
    super().__init__(type_id)
    self.fmt = fmt
    self.padded = padded

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    data = _struct.pack(self.fmt, value)
    return params._add_fragment(self.type_id, data, 0x01 if self.padded else 0)

  def decode_from(self, data: bytes) -> Any:
    size = _struct.calcsize(self.fmt)
    val = _struct.unpack(self.fmt, data[:size])[0]
    if self.type_id == HamiltonDataType.BOOL:
      return bool(val)
    if self.type_id in (
      HamiltonDataType.F32,
      HamiltonDataType.F64,
    ):
      return float(val)
    return int(val)


class Array(WireType):
  """Homogeneous array of packed scalars (no length prefix on the wire)."""

  __slots__ = ("element_fmt",)

  def __init__(self, type_id: int, element_fmt: str):
    super().__init__(type_id)
    self.element_fmt = element_fmt

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    data = _struct.pack(f"{len(value)}{self.element_fmt}", *value)
    flags = 0x01 if self.type_id == HamiltonDataType.BOOL_ARRAY else 0
    return params._add_fragment(self.type_id, data, flags)

  def decode_from(self, data: bytes) -> Any:
    el_size = _struct.calcsize(self.element_fmt)
    count = len(data) // el_size
    values = _struct.unpack(f"{count}{self.element_fmt}", data[: count * el_size])
    if self.type_id == HamiltonDataType.BOOL_ARRAY:
      return [bool(v) for v in values]
    return list(values)


class Struct(WireType):
  """Nested structure -- recurse via ``HoiParams.from_struct``."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRUCTURE)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams as HP

    return params._add_fragment(self.type_id, HP.from_struct(value).build())

  def decode_from(self, data: bytes) -> Any:
    return data


class StructArray(WireType):
  """Array of nested structures."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRUCTURE_ARRAY)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams as HP

    inner = b""
    for v in value:
      payload = HP.from_struct(v).build()
      inner += _struct.pack("<BBH", HamiltonDataType.STRUCTURE, 0, len(payload)) + payload
    return params._add_fragment(self.type_id, inner)

  def decode_from(self, data: bytes) -> Any:
    # Parse concatenated Structure sub-fragments: [type_id:1][flags:1][length:2][data:N]
    out: list[bytes] = []
    off = 0
    while off + 4 <= len(data):
      type_id = data[off]
      length = int.from_bytes(data[off + 2 : off + 4], "little")
      off += 4
      if off + length > len(data):
        break
      if type_id == HamiltonDataType.STRUCTURE:
        out.append(data[off : off + length])
      off += length
    return out


class CountedFlatArray(WireType):
  """Count-prefix array where elements share the caller's parser stream.

  Decode-only (introspection protocol uses this; domain commands use StructArray).
  """

  __slots__ = ()

  def __init__(self):
    super().__init__(type_id=-1)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    raise NotImplementedError("CountedFlatArray is decode-only (introspection protocol)")


@dataclass(frozen=True)
class HcResultEntry:
  """One channel's entry in a multi-channel ``NetworkType::HoiResult``.

  Source: ``decompiled/.../NetworkDefinedType.cs`` (6 parallel arrays) +
  ``HcResultEx.cs`` (bit layout). ``result`` is the raw u16 HcResult code;
  the high bit (0x8000) flags a warning, bits 8-11 encode error category.
  """

  module_id: int
  node_id: int
  object_id: int
  interface_id: int
  action_id: int
  result: int

  @property
  def is_warning(self) -> bool:
    return bool(self.result & 0x8000)

  @property
  def is_success(self) -> bool:
    return self.result == 0 or self.is_warning

  @property
  def address(self) -> tuple[int, int, int]:
    return (self.module_id, self.node_id, self.object_id)


class StringType(WireType):
  """Null-terminated ASCII string."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRING)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    data = value.encode("utf-8") + b"\x00"
    return params._add_fragment(self.type_id, data)

  def decode_from(self, data: bytes) -> Any:
    return data.rstrip(b"\x00").decode("utf-8")


class StringArrayType(WireType):
  """Array of null-terminated strings (type_id=34).

  Wire format: payload is a concatenation of null-terminated UTF-8 strings with
  no leading element count. Fragment length in the HOI header defines the
  payload boundary.
  """

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRING_ARRAY)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    data = b""
    for s in value:
      data += s.encode("utf-8") + b"\x00"
    return params._add_fragment(self.type_id, data)

  def decode_from(self, data: bytes) -> Any:
    if not data:
      return []
    out: list[str] = []
    off = 0
    while off < len(data):
      null_pos = data.find(b"\x00", off)
      if null_pos == -1:
        break
      out.append(data[off:null_pos].decode("utf-8"))
      off = null_pos + 1
    return out


# ---------------------------------------------------------------------------
# Annotated type aliases
# ---------------------------------------------------------------------------

# Scalars  (mypy sees the base Python type: int / float / bool / str)
I8 = Annotated[int, Scalar(HamiltonDataType.I8, "b")]
I16 = Annotated[int, Scalar(HamiltonDataType.I16, "h")]
I32 = Annotated[int, Scalar(HamiltonDataType.I32, "i")]
I64 = Annotated[int, Scalar(HamiltonDataType.I64, "q")]
U8 = Annotated[int, Scalar(HamiltonDataType.U8, "B")]
U16 = Annotated[int, Scalar(HamiltonDataType.U16, "H")]
U32 = Annotated[int, Scalar(HamiltonDataType.U32, "I")]
U64 = Annotated[int, Scalar(HamiltonDataType.U64, "Q")]
F32 = Annotated[float, Scalar(HamiltonDataType.F32, "f")]
F64 = Annotated[float, Scalar(HamiltonDataType.F64, "d")]
Bool = Annotated[bool, Scalar(HamiltonDataType.BOOL, "?")]
Enum = Annotated[int, Scalar(HamiltonDataType.ENUM, "I")]
HcResult = Annotated[int, Scalar(HamiltonDataType.HC_RESULT, "H")]
Str = Annotated[str, StringType()]

# Prep-padded variants (Bool and U8 are always padded on Prep hardware)
PaddedBool = Annotated[bool, Scalar(HamiltonDataType.BOOL, "?", padded=True)]
PaddedU8 = Annotated[int, Scalar(HamiltonDataType.U8, "B", padded=True)]

# Arrays  (mypy sees ``list``)
I8Array = Annotated[list, Array(HamiltonDataType.I8_ARRAY, "b")]
I16Array = Annotated[list, Array(HamiltonDataType.I16_ARRAY, "h")]
I32Array = Annotated[list, Array(HamiltonDataType.I32_ARRAY, "i")]
I64Array = Annotated[list, Array(HamiltonDataType.I64_ARRAY, "q")]
U8Array = Annotated[list, Array(HamiltonDataType.U8_ARRAY, "B")]
U16Array = Annotated[list, Array(HamiltonDataType.U16_ARRAY, "H")]
U32Array = Annotated[list, Array(HamiltonDataType.U32_ARRAY, "I")]
U64Array = Annotated[list, Array(HamiltonDataType.U64_ARRAY, "Q")]
F32Array = Annotated[list, Array(HamiltonDataType.F32_ARRAY, "f")]
F64Array = Annotated[list, Array(HamiltonDataType.F64_ARRAY, "d")]
BoolArray = Annotated[list, Array(HamiltonDataType.BOOL_ARRAY, "?")]
EnumArray = Annotated[list, Array(HamiltonDataType.ENUM_ARRAY, "I")]
StrArray = Annotated[list, StringArrayType()]

# Compound types: Structure and StructureArray do NOT have simple aliases
# because ``Annotated[object, Struct()]`` would erase the concrete type for
# mypy.  Use inline ``Annotated[ConcreteType, Struct()]`` on each field to
# preserve full type safety.  The class singletons are exported so call-sites
# only need ``Struct()`` and ``StructArray()``.

# ---------------------------------------------------------------------------
# Type registry and decode_fragment
# ---------------------------------------------------------------------------

_WIRE_TYPE_REGISTRY: dict[int, WireType] = {}


def _register(alias: type) -> None:
  meta = getattr(alias, "__metadata__", (None,))[0]
  assert meta is not None, f"Expected Annotated alias with metadata: {alias}"
  _WIRE_TYPE_REGISTRY[meta.type_id] = meta


for _alias in [
  I8,
  I16,
  I32,
  I64,
  U8,
  U16,
  U32,
  U64,
  F32,
  F64,
  Bool,
  Enum,
  HcResult,
  Str,
  I8Array,
  I16Array,
  I32Array,
  I64Array,
  U8Array,
  U16Array,
  U32Array,
  U64Array,
  F32Array,
  F64Array,
  BoolArray,
  EnumArray,
  StrArray,
]:
  _register(_alias)

_WIRE_TYPE_REGISTRY[HamiltonDataType.STRUCTURE] = Struct()
_WIRE_TYPE_REGISTRY[HamiltonDataType.STRUCTURE_ARRAY] = StructArray()


def decode_fragment(type_id: int, data: bytes) -> Any:
  """Decode a DataFragment payload using the unified type registry."""
  wt = _WIRE_TYPE_REGISTRY.get(type_id)
  if wt is None:
    raise ValueError(f"Unknown DataFragment type_id: {type_id}")
  return wt.decode_from(data)
