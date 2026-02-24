"""Wire type system for Hamilton DataFragment encoding.

Provides polymorphic WireType objects used as ``typing.Annotated`` metadata on
dataclass fields.  A single generic serialiser (``HoiParams.from_struct``)
reads these annotations and delegates to ``WireType.encode_into`` -- no
if/elif chains, no lambda dispatch tables.

Usage on a dataclass field::

    @dataclass
    class DropTipParameters:
        default_values: PaddedBool
        channel: Enum
        y_position: F32

Serialise with::

    HoiParams.from_struct(drop_tip_params)
"""

from __future__ import annotations

import struct as _struct
from typing import TYPE_CHECKING, Annotated

from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonDataType

if TYPE_CHECKING:
  from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams


# ---------------------------------------------------------------------------
# WireType hierarchy
# ---------------------------------------------------------------------------


class WireType:
  """Base class: a wire-format type that can encode values into HoiParams."""

  __slots__ = ("type_id",)

  def __init__(self, type_id: int):
    self.type_id = type_id

  def encode_into(self, value, params: HoiParams) -> HoiParams:
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
    if self.padded:
      data += b"\x00"
    return params._add_fragment(self.type_id, data, 0x01 if self.padded else 0)


class Array(WireType):
  """Homogeneous array of packed scalars (no length prefix on the wire)."""

  __slots__ = ("element_fmt",)

  def __init__(self, type_id: int, element_fmt: str):
    super().__init__(type_id)
    self.element_fmt = element_fmt

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    data = _struct.pack(f"{len(value)}{self.element_fmt}", *value)
    return params._add_fragment(self.type_id, data)


class Struct(WireType):
  """Nested structure -- recurse via ``HoiParams.from_struct``."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRUCTURE)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams as HP

    return params.structure(HP.from_struct(value))


class StructArray(WireType):
  """Array of nested structures."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRUCTURE_ARRAY)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams as HP

    return params.structure_array([HP.from_struct(v) for v in value])


class StringType(WireType):
  """Null-terminated ASCII string."""

  __slots__ = ()

  def __init__(self):
    super().__init__(HamiltonDataType.STRING)

  def encode_into(self, value, params: HoiParams) -> HoiParams:
    return params.string(value)


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

# Compound types: Structure and StructureArray do NOT have simple aliases
# because ``Annotated[object, Struct()]`` would erase the concrete type for
# mypy.  Use inline ``Annotated[ConcreteType, Struct()]`` on each field to
# preserve full type safety.  The class singletons are exported so call-sites
# only need ``Struct()`` and ``StructArray()``.
