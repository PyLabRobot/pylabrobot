"""Hamilton TCP Introspection API.

Provides dynamic discovery via Interface 0 methods (GetObject, GetMethod,
GetStructs, GetEnums, GetInterfaces, GetSubobjectAddress).

:class:`HamiltonIntrospection` receives its transport dependencies (registry,
send_discovery_command, send_query) as explicit callables — no back-reference
to the client. The client constructs it via the lazy
:attr:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient.introspection` property,
which is the **only** supported entry point from application code.

**Runtime defaults (lazy, cache-friendly):**

- :meth:`~HamiltonIntrospection.ensure_method_table` /
  :meth:`~HamiltonIntrospection.methods_for_interface` — scan GetMethod once per object.
- :meth:`~HamiltonIntrospection.ensure_structs_enums` — fetch GetStructs/GetEnums per
  HO interface when needed (e.g. for signature resolution).
- :meth:`~HamiltonIntrospection.ensure_global_type_pool` — build
  :class:`GlobalTypePool` once per session for ``source_id=1`` refs.
- :meth:`~HamiltonIntrospection.resolve_signature` — resolves a method string without
  a pre-built :class:`TypeRegistry` (unless you pass one).

**Export / parity / codegen (eager composed dumps):**

- :meth:`~HamiltonIntrospection.build_type_registry` — full structs/enums per
  interface (same wire as composing :meth:`~HamiltonIntrospection.ensure_structs_enums`
  for each iface).
- :meth:`~HamiltonIntrospection.build_global_type_pool` — full global walk (does not
  use the session singleton; use :meth:`~HamiltonIntrospection.ensure_global_type_pool`
  for lazy ``source_id=1`` resolution).

Example (typical notebook)::

    client = HamiltonTCPClient(host=..., port=...)
    await client.setup()
    intro = client.introspection
    sig = await intro.resolve_signature("MLPrepRoot.MphRoot.MPH", 1, 9)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Set, Tuple, Union, cast

from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.messages import (
  PADDED_FLAG,
  HoiParams,
  HoiParamsParser,
  inspect_hoi_params,
)
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.tcp.wire_types import (
  U16,
  U32,
  HamiltonDataType,
  I8Array,
  I32Array,
  Str,
  StrArray,
  U8Array,
  U32Array,
)

logger = logging.getLogger(__name__)


class Direction(IntEnum):
  """Direction of a method parameter in the HOI introspection type system.

  Column order matches ``_HOI_TYPE_ROWS`` ids tuple: ``ids[Direction]`` gives the
  direction-encoded HOI type ID for that row and direction.
  """

  In = 0
  Out = 1
  InOut = 2
  RetVal = 3



async def _subobject_address_and_info(
  intro: "HamiltonIntrospection", parent_addr: Address, index: int
) -> Tuple[Address, ObjectInfo]:
  """Resolve one subobject index to ``(address, ObjectInfo)`` (shared resolve/tree path)."""
  sub_addr = await intro.get_subobject_address(parent_addr, index)
  sub_info = await intro.get_object(sub_addr)
  return sub_addr, sub_info


# Connection/transport errors that should propagate immediately rather than
# being swallowed by introspection catch blocks. A dead connection would
# otherwise cause N individual timeouts (one per method) before the caller
# sees any error.
_TRANSIENT_ERRORS = (
  TimeoutError,
  ConnectionError,
  ConnectionResetError,
  ConnectionAbortedError,
  BrokenPipeError,
  OSError,
)

# Known network/built-in structs (source_id=3). These types are not queryable
# via introspection — their wire format was determined empirically by calling
# methods that return them (e.g. GetDeckCalibration on PipettorCalibration).
# Populated lazily below after StructInfo is defined.
_NETWORK_STRUCTS: Dict[int, "StructInfo"] = {}

# ============================================================================
# TYPE RESOLUTION HELPERS
# ============================================================================


def resolve_type_id(type_id: int) -> str:
  """Resolve Hamilton type ID to readable name.

  Args:
      type_id: Hamilton data type ID

  Returns:
      Human-readable type name
  """
  try:
    return cast(str, HamiltonDataType(type_id).name)
  except ValueError:
    return f"UNKNOWN_TYPE_{type_id}"


# ============================================================================
# INTROSPECTION TYPE MAPPING (2D table from HoiObject.mHoiParamTypes)
# ============================================================================
# Each row maps one wire kind (HamiltonDataType) × 4 directions (Direction enum)
# to the direction-encoded HOI type IDs the firmware uses in GetMethod responses.
# Source: vendor protocol reference mHoiParamTypes[31,4].


@dataclass(frozen=True)
class _HoiTypeRow:
  """One row in vendor mHoiParamTypes[31,4] with readable display metadata.

  ``ids`` always follows the interface-0 type table column order:
  ``(In, Out, InOut, RetVal)``. These columns are specific to the firmware's
  interface-0 HOI type system, a unique typing scheme separate from the
  standard ``HamiltonDataType`` wire type IDs.

  ``wire_type``: the ``HamiltonDataType`` that this HOI kind maps to on the wire.
  This is the bridge between the two type systems: HOI introspection IDs are
  direction-encoded variants of a ``wire_type`` kind.

  ``is_complex``: type requires additional source_id/ref_id bytes in method param encoding.
  ``is_struct_kind``: type references a struct definition (subset of complex).
  ``is_enum_kind``: type references an enum definition (subset of complex).
  """

  display_name: str
  ids: tuple[int, int, int, int]  # Interface-0 column order: [In, Out, InOut, RetVal]
  wire_type: HamiltonDataType = HamiltonDataType.VOID
  is_complex: bool = False
  is_struct_kind: bool = False
  is_enum_kind: bool = False


_HOI_TYPE_ROWS: tuple[_HoiTypeRow, ...] = (
  _HoiTypeRow("i8",         (1, 17, 9, 25),       HamiltonDataType.I8),
  _HoiTypeRow("i16",        (3, 19, 11, 27),      HamiltonDataType.I16),
  _HoiTypeRow("i32",        (5, 21, 13, 29),      HamiltonDataType.I32),
  _HoiTypeRow("u8",         (2, 18, 10, 26),      HamiltonDataType.U8),
  _HoiTypeRow("u16",        (4, 20, 12, 28),      HamiltonDataType.U16),
  _HoiTypeRow("u32",        (6, 22, 14, 30),      HamiltonDataType.U32),
  _HoiTypeRow("str",        (7, 23, 15, 31),      HamiltonDataType.STRING),
  _HoiTypeRow("bool",       (33, 35, 34, 36),     HamiltonDataType.BOOL),
  _HoiTypeRow("List[i8]",   (37, 39, 38, 40),     HamiltonDataType.I8_ARRAY),
  _HoiTypeRow("List[i16]",  (41, 43, 42, 44),     HamiltonDataType.I16_ARRAY),
  _HoiTypeRow("List[i32]",  (49, 51, 50, 52),     HamiltonDataType.I32_ARRAY),
  _HoiTypeRow("bytes",      (8, 24, 16, 32),      HamiltonDataType.U8_ARRAY),
  _HoiTypeRow("List[u16]",  (45, 47, 46, 48),     HamiltonDataType.U16_ARRAY),
  _HoiTypeRow("List[u32]",  (53, 55, 54, 56),     HamiltonDataType.U32_ARRAY),
  _HoiTypeRow("List[bool]", (66, 68, 67, 69),     HamiltonDataType.BOOL_ARRAY),
  _HoiTypeRow("HcResult",   (70, 72, 71, 73),     HamiltonDataType.HC_RESULT,   is_complex=True),
  _HoiTypeRow("struct",     (57, 59, 58, 60),     HamiltonDataType.STRUCTURE,   is_complex=True, is_struct_kind=True),
  _HoiTypeRow("List[struct]",(61, 63, 62, 64),    HamiltonDataType.STRUCTURE_ARRAY, is_complex=True, is_struct_kind=True),
  _HoiTypeRow("List[str]",  (74, 76, 75, 77),     HamiltonDataType.STRING_ARRAY, is_complex=True),
  _HoiTypeRow("enum",       (78, 80, 79, 81),     HamiltonDataType.ENUM,        is_complex=True, is_enum_kind=True),
  _HoiTypeRow("List[enum]", (82, 84, 83, 85),     HamiltonDataType.ENUM_ARRAY,  is_complex=True, is_enum_kind=True),
  _HoiTypeRow("i64",        (86, 88, 87, 89),     HamiltonDataType.I64),
  _HoiTypeRow("u64",        (90, 92, 91, 93),     HamiltonDataType.U64),
  _HoiTypeRow("f32",        (94, 96, 95, 97),     HamiltonDataType.F32),
  _HoiTypeRow("f64",        (98, 100, 99, 101),   HamiltonDataType.F64),
  _HoiTypeRow("List[i64]",  (102, 104, 103, 105), HamiltonDataType.I64_ARRAY),
  _HoiTypeRow("List[u64]",  (106, 108, 107, 109), HamiltonDataType.U64_ARRAY),
  _HoiTypeRow("List[f32]",  (110, 112, 111, 113), HamiltonDataType.F32_ARRAY),
  _HoiTypeRow("List[f64]",  (114, 116, 115, 117), HamiltonDataType.F64_ARRAY),
  _HoiTypeRow("HoiResult",  (118, 120, 119, 121), HamiltonDataType.HOI_RESULT, is_complex=True),
  _HoiTypeRow("padding",    (0, 0, 0, 0),         HamiltonDataType.VOID),
)

# HOI method-param type IDs that require extra source_id/ref_id bytes on the wire
# (rows where is_complex=True). Used as a parsing guard in _parse_method_param_types.
_COMPLEX_METHOD_TYPE_IDS: frozenset[int] = frozenset(
  tid for row in _HOI_TYPE_ROWS if row.is_complex for tid in row.ids if tid != 0
)

# GetStructs wire sentinels for complex field types (HamiltonDataType namespace, not HOI).
# Used as a parsing guard in _parse_struct_field_types.
_COMPLEX_STRUCT_TYPE_IDS: frozenset[int] = frozenset(
  {
    HamiltonDataType.STRUCTURE,
    HamiltonDataType.STRUCTURE_ARRAY,
    HamiltonDataType.ENUM,
    HamiltonDataType.ENUM_ARRAY,
  }
)

# Reverse lookup: direction-encoded HOI ID → (wire_type, Direction).
# Built from _HOI_TYPE_ROWS: each row encodes one wire kind × 4 directions.
# This is the bridge between the HOI introspection namespace and HamiltonDataType.
_HOI_ID_TO_WIRE: Dict[int, Tuple[HamiltonDataType, Direction]] = {}
for _row in _HOI_TYPE_ROWS:
  for _ci, _tid in enumerate(_row.ids):
    if _tid != 0:
      _HOI_ID_TO_WIRE[_tid] = (_row.wire_type, Direction(_ci))
# Empirical: ID 113 (List[f32] RetVal column) observed as In argument on some firmware.
# TODO: Re-validate against hardware captures and remove if no longer observed.
_HOI_ID_TO_WIRE[113] = (HamiltonDataType.F32_ARRAY, Direction.In)



# ============================================================================
# DATA STRUCTURES
# ============================================================================


@dataclass
class ObjectInfo:
  """Object metadata from introspection."""

  name: str
  version: str
  method_count: int
  subobject_count: int
  address: Address
  children: Dict[str, "ObjectInfo"] = field(default_factory=dict)


class ObjectRegistry:
  """Pure key-value cache: path ↔ ObjectInfo and address → path.

  No async logic; all traversal lives in :class:`HamiltonIntrospection`.
  """

  def __init__(self):
    self._objects: Dict[str, ObjectInfo] = {}
    self._address_to_path: Dict[Address, str] = {}
    self._root_address: Optional[Address] = None

  def set_root_address(self, address: Address) -> None:
    self._root_address = address

  def get_root_address(self) -> Optional[Address]:
    return self._root_address

  def register(self, path: str, obj: ObjectInfo) -> None:
    self._objects[path] = obj
    self._address_to_path[obj.address] = path

  def address_for(self, path: str) -> Optional[Address]:
    obj = self._objects.get(path)
    return obj.address if obj is not None else None

  def path(self, address: Address) -> Optional[str]:
    return self._address_to_path.get(address)


@dataclass
class FirmwareTreeNode:
  """One node in a discovered firmware object tree."""

  path: str
  address: Address
  object_info: ObjectInfo
  supported_interface0_methods: Set[int] = field(default_factory=set)
  children: List["FirmwareTreeNode"] = field(default_factory=list)

  def format_lines(
    self, prefix: str = "", is_last: bool = True, is_root: bool = False
  ) -> List[str]:
    # Most objects expose the full Interface-0 contract (1..6). Hide it in
    # default rendering to keep large trees readable; only show deviations.
    full_i0_contract = {1, 2, 3, 4, 5, 6}
    show_i0 = self.supported_interface0_methods != full_i0_contract
    i0_suffix = ""
    if show_i0:
      method_ids = ",".join(str(v) for v in sorted(self.supported_interface0_methods))
      i0_suffix = f", i0=[{method_ids}]"
    branch = "" if is_root else ("└─ " if is_last else "├─ ")
    lines = [
      f"{prefix}{branch}{self.path} @ {self.address} "
      f"(methods={self.object_info.method_count}, subobjects={self.object_info.subobject_count}"
      f"{i0_suffix})"
    ]
    child_prefix = prefix + ("   " if is_last or is_root else "│  ")
    for idx, child in enumerate(self.children):
      child_is_last = idx == len(self.children) - 1
      lines.extend(child.format_lines(prefix=child_prefix, is_last=child_is_last, is_root=False))
    return lines

  def __str__(self) -> str:
    return "\n".join(self.format_lines())


@dataclass
class FirmwareTree:
  """Structured firmware tree produced by introspection traversal.

  Both Prep and Nimbus expose exactly one root object; the single-root
  invariant is enforced at discovery time in the TCP client.
  """

  root: FirmwareTreeNode

  def format(self) -> str:
    return "\n".join(self.root.format_lines(prefix="", is_last=True, is_root=True))

  def __str__(self) -> str:
    return self.format()


def flatten_firmware_tree(tree: FirmwareTree) -> List[Tuple[str, Address, ObjectInfo]]:
  """Preorder flattening of :class:`FirmwareTree` for path-keyed lookups.

  Returns ``(dot_path, address, object_info)`` for each node (root first, DFS).
  """
  out: List[Tuple[str, Address, ObjectInfo]] = []

  def walk(node: FirmwareTreeNode) -> None:
    out.append((node.path, node.address, node.object_info))
    for child in node.children:
      walk(child)

  walk(tree.root)
  return out



@dataclass
class MethodParamType:
  """A method parameter or return type from GetMethod, in the HOI introspection namespace.

  ``wire_type`` is the ``HamiltonDataType`` this HOI kind maps to on the wire —
  the bridge between the direction-encoded HOI IDs and the wire encoding layer.
  ``direction`` records whether this entry is In/Out/InOut/RetVal in the method signature.

  Struct/enum references additionally carry source_id and ref_id:
    source_id 1=global, 2=local, 3=network, 4=node-global.
    ref_id is the struct/enum index within the pool identified by source_id.
  """

  wire_type: HamiltonDataType
  direction: Direction
  source_id: Optional[int] = None
  ref_id: Optional[int] = None
  _byte_width: int = 1  # bytes consumed from the wire blob

  @property
  def is_struct_ref(self) -> bool:
    return self.wire_type in (HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE_ARRAY)

  @property
  def is_enum_ref(self) -> bool:
    return self.wire_type in (HamiltonDataType.ENUM, HamiltonDataType.ENUM_ARRAY)

  @property
  def is_argument(self) -> bool:
    """True if this is an input parameter (In or InOut)."""
    return self.direction in (Direction.In, Direction.InOut)

  @property
  def is_return(self) -> bool:
    """True if this is a return value (Out or RetVal)."""
    return self.direction in (Direction.Out, Direction.RetVal)

  def resolve_name(
    self,
    registry: Optional["TypeRegistry"] = None,
    ho_interface_id: Optional[int] = None,
  ) -> str:
    """Resolve to a human-readable name, optionally using a TypeRegistry for struct/enum names."""
    base = self.wire_type.name.lower()
    if self.source_id is None or self.ref_id is None:
      return base
    if self.is_struct_ref:
      if registry is not None:
        s = registry.resolve_struct(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
        if s:
          return s.name
      return f"{base}(iface={self.source_id}, id={self.ref_id})"
    if self.is_enum_ref:
      if registry is not None:
        e = registry.resolve_enum(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
        if e:
          return e.name
      return f"{base}(iface={self.source_id}, id={self.ref_id})"
    return f"{base}(iface={self.source_id}, id={self.ref_id})"


def _parse_method_param_types(
  data: bytes | list[int],
) -> List[MethodParamType]:
  """Parse GetMethod parameterTypes byte stream.

  Source: HoiObject.HandleStruct in HoiObject.cs.

  Encoding per entry:
  - Simple type (not in _COMPLEX_METHOD_TYPE_IDS): ``[type_id]`` — 1 byte.
  - source_id 1/2/3 (global/local/network): ``[type_id, source_id, ref_id]`` — 3 bytes.
  - source_id 4 (node-global): ``[type_id, 4, index, '"', FormatAddress_bytes..., '"', ' ']``.
    FormatAddress encodes Module+Node as hex byte pairs, wrapped in ASCII double-quotes.
    The index byte is the struct/enum index within the node-global pool.
  """
  _NODE_GLOBAL = 4
  _QUOTE = 0x22
  _SPACE = 0x20

  ints = list(data) if isinstance(data, bytes) else data
  result: List[MethodParamType] = []
  i = 0
  while i < len(ints):
    tid = ints[i]
    wire_type, direction = _HOI_ID_TO_WIRE.get(tid, (HamiltonDataType.VOID, Direction.In))
    if tid in _COMPLEX_METHOD_TYPE_IDS and i + 2 < len(ints):
      source_id = ints[i + 1]
      ref_id = ints[i + 2]
      if source_id == _NODE_GLOBAL:
        # [type_id, 4, index, '"', FormatAddress_bytes..., '"', ' ']
        end = i + 4  # byte after opening '"'
        while end < len(ints) and ints[end] != _QUOTE:
          end += 1
        end += 1  # consume closing '"'
        if end < len(ints) and ints[end] == _SPACE:
          end += 1  # consume trailing ' '
        result.append(
          MethodParamType(wire_type, direction, source_id=_NODE_GLOBAL, ref_id=ref_id, _byte_width=end - i)
        )
        i = end
      else:
        result.append(MethodParamType(wire_type, direction, source_id=source_id, ref_id=ref_id, _byte_width=3))
        i += 3
    else:
      result.append(MethodParamType(wire_type, direction))
      i += 1
  return result


@dataclass
class StructFieldType:
  """A struct field type from GetStructs, in the HamiltonDataType wire namespace.

  ``type_id`` is a ``HamiltonDataType`` value — the wire encoding type for this field.
  Unlike ``MethodParamType``, struct fields have no direction concept.

  Complex references (STRUCTURE/ENUM) additionally carry source_id and ref_id:
    source_id 1=global, 2=local, 3=network, 4=node-global.
    ref_id is the struct/enum index within the pool identified by source_id.
  """

  type_id: HamiltonDataType
  source_id: Optional[int] = None
  ref_id: Optional[int] = None
  _byte_width: int = 1  # bytes consumed from the wire blob (1=simple, 3=ref, 7=node-global)

  @property
  def is_complex(self) -> bool:
    return self.type_id in (
      HamiltonDataType.STRUCTURE,
      HamiltonDataType.STRUCTURE_ARRAY,
      HamiltonDataType.ENUM,
      HamiltonDataType.ENUM_ARRAY,
    )

  @property
  def is_struct_ref(self) -> bool:
    return self.type_id in (HamiltonDataType.STRUCTURE, HamiltonDataType.STRUCTURE_ARRAY)

  @property
  def is_enum_ref(self) -> bool:
    return self.type_id in (HamiltonDataType.ENUM, HamiltonDataType.ENUM_ARRAY)

  def resolve_name(
    self,
    registry: Optional["TypeRegistry"] = None,
    ho_interface_id: Optional[int] = None,
  ) -> str:
    """Resolve to a human-readable type name, optionally using a TypeRegistry for struct/enum names."""
    if self.is_complex and self.source_id is not None and self.ref_id is not None:
      if registry is not None:
        if self.is_struct_ref:
          s = registry.resolve_struct(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
          if s:
            return f"struct({s.name})"
        elif self.is_enum_ref:
          e = registry.resolve_enum(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
          if e:
            return e.name
      return f"ref(iface={self.source_id}, id={self.ref_id})"
    return resolve_type_id(self.type_id)


def _parse_struct_field_types(
  data: bytes | list[int],
) -> List[StructFieldType]:
  """Parse GetStructs structureElementTypes byte stream.

  Source: HoiObject.GetStructs in HoiObject.cs.

  Encoding per entry:
  - Simple type (not in _COMPLEX_STRUCT_TYPE_IDS): ``[type_id]`` — 1 byte.
  - source_id 1/2/3 (global/local/network): ``[type_id, source_id, ref_id]`` — 3 bytes.
  - source_id 4 (node-global, scope.mAddress.ModuleID != 0):
    ``[type_id, 4, index, ModHi, ModLo, NodeHi, NodeLo]`` — 7 bytes.
    The 4 raw address bytes are written when the node-global object has a non-zero
    ModuleID, which is always true for real node-global objects on this instrument.
  """
  _NODE_GLOBAL = 4
  _NODE_GLOBAL_WIDTH = 7

  ints = list(data) if isinstance(data, bytes) else data
  result: List[StructFieldType] = []
  i = 0
  while i < len(ints):
    tid = ints[i]
    wire_type = HamiltonDataType(tid)
    if tid in _COMPLEX_STRUCT_TYPE_IDS and i + 2 < len(ints):
      source_id = ints[i + 1]
      ref_id = ints[i + 2]
      if source_id == _NODE_GLOBAL:
        # [type_id, 4, index, ModHi, ModLo, NodeHi, NodeLo] = 7 bytes
        result.append(
          StructFieldType(wire_type, source_id=_NODE_GLOBAL, ref_id=ref_id, _byte_width=_NODE_GLOBAL_WIDTH)
        )
        i += _NODE_GLOBAL_WIDTH
      else:
        result.append(StructFieldType(wire_type, source_id=source_id, ref_id=ref_id, _byte_width=3))
        i += 3
    else:
      result.append(StructFieldType(wire_type))
      i += 1
  return result


def _parse_type_ids(raw: str | bytes | None) -> List[MethodParamType]:
  """Parse GetMethod parameterTypes blob. Thin wrapper around _parse_method_param_types.

  Accepts bytes (preferred) or str — the device sends STRING (15) but the
  payload is binary, so callers must use parse_next_raw() to avoid UTF-8 errors.
  """
  if raw is None:
    return []
  data: list[int] = list(raw) if isinstance(raw, bytes) else [ord(c) for c in raw]
  return _parse_method_param_types(data)


@dataclass
class MethodFieldDescriptor:
  """Canonical representation of one method parameter/return field."""

  name: str
  type_name: str


@dataclass
class MethodDescriptor:
  """Canonical normalized representation of a method signature."""

  interface_id: int
  method_id: int
  name: str
  params: list[MethodFieldDescriptor] = field(default_factory=list)
  returns: list[MethodFieldDescriptor] = field(default_factory=list)
  return_shape: Literal["void", "scalar", "record"] = "void"

  @property
  def id_string(self) -> str:
    return f"[{self.interface_id}:{self.method_id}]"

  def signature_string(self) -> str:
    """Render the canonical method descriptor as a signature string."""
    if self.params:
      param_str = ", ".join(f"{p.name}: {p.type_name}" for p in self.params)
    else:
      param_str = "void"

    if self.return_shape == "void" or not self.returns:
      return_str = "void"
    elif self.return_shape == "scalar" and len(self.returns) == 1:
      ret = self.returns[0]
      return_str = f"{ret.name}: {ret.type_name}" if ret.name != "ret0" else ret.type_name
    else:
      return_str = (
        "{ "
        + ", ".join(f"{r.name}: {r.type_name}" for r in self.returns)
        + " }"
      )

    return f"{self.id_string} {self.name}({param_str}) -> {return_str}"

  def to_dict(self) -> dict:
    return {
      "name": self.name,
      "id": self.id_string,
      "signature": self.signature_string(),
      "params": [{"name": p.name, "type": p.type_name} for p in self.params],
      "returns": [{"name": r.name, "type": r.type_name} for r in self.returns],
    }


@dataclass
class MethodInfo:
  """Method signature from introspection."""

  interface_id: int
  call_type: int
  method_id: int
  name: str
  parameter_types: list[MethodParamType] = field(default_factory=list)
  parameter_labels: list[str] = field(default_factory=list)
  return_types: list[MethodParamType] = field(default_factory=list)
  return_labels: list[str] = field(default_factory=list)

  def describe(self, registry: Optional["TypeRegistry"] = None) -> MethodDescriptor:
    """Return the canonical normalized method descriptor used by all serializers."""
    iid = self.interface_id
    params: list[MethodFieldDescriptor] = []
    if self.parameter_types:
      param_type_names = [
        pt.resolve_name(registry, ho_interface_id=iid) for pt in self.parameter_types
      ]
      for i, type_name in enumerate(param_type_names):
        label = self.parameter_labels[i] if i < len(self.parameter_labels) else None
        params.append(MethodFieldDescriptor(name=label or f"arg{i}", type_name=type_name))

    returns: list[MethodFieldDescriptor] = []
    return_shape: Literal["void", "scalar", "record"] = "void"
    if self.return_types:
      return_type_names = [
        rt.resolve_name(registry, ho_interface_id=iid) for rt in self.return_types
      ]
      for i, type_name in enumerate(return_type_names):
        label = self.return_labels[i] if i < len(self.return_labels) else None
        returns.append(MethodFieldDescriptor(name=label or f"ret{i}", type_name=type_name))
      if len(returns) == 1 and not any(rt.direction == Direction.Out for rt in self.return_types):
        return_shape = "scalar"
      elif len(returns) > 0:
        # Includes Out/ReturnElement records and explicit multi-return methods.
        return_shape = "record"

    return MethodDescriptor(
      interface_id=self.interface_id,
      method_id=self.method_id,
      name=self.name,
      params=params,
      returns=returns,
      return_shape=return_shape,
    )

  def get_signature_string(self, registry: Optional["TypeRegistry"] = None) -> str:
    """Get method signature as a readable string.

    If a TypeRegistry is provided, struct/enum references are resolved to
    their names (e.g. PickupTipParameters instead of structure(source=2, ref=1)).
    """
    return self.describe(registry).signature_string()

  def to_dict(self, registry: Optional["TypeRegistry"] = None) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    return self.describe(registry).to_dict()


@dataclass
class TypeRegistry:
  """Resolved type information for one object.

  Built once from introspection during setup. Caches structs, enums, and
  interface info so method signatures can be fully resolved without additional
  device calls. Use build_type_registry() to create.

  Source ID semantics (from piglet — the middle byte of a struct/enum type triple):
    source_id=1: Global pool (shared type definitions from global objects); ref_id is
      1-based into that flat list (see GlobalTypePool.resolve_struct).
    source_id=2: Local types on this object; ref_id is 1-based into the per-interface
      struct/enum maps in self.structs / self.enums (struct_id / enum_id from GetStructs
      / GetEnums is 0-based). This is NOT ``HOI interface id 2``; ``2`` means *local*
      in the type encoding; tables are keyed by real interface ids (typically ``1`` for
      ``[1:*]`` methods alongside introspection on ``0``).
    source_id=3: Built-in / network types (e.g. NetworkResult-shaped); resolve_struct
      does not decode these yet — validate behavior vs Piglet or device captures.

  source_id=0 is not emitted by firmware; treat any such ref as unresolvable.

  For source_id=2, pass ``ho_interface_id`` on ``resolve_struct`` / ``resolve_enum`` so
  lookup is strict to the owning interface's local table.

  Example (full export registry)::

    registry = await intro.build_type_registry(mph_addr)
    method = registry.get_method(interface_id=1, method_id=9)
    print(method.get_signature_string(registry))  # PickupTips(tipParameters: PickupTipParameters, ...)

  For notebooks and runtime tooling, prefer :meth:`~HamiltonIntrospection.resolve_signature`
  (lazy types) instead of building a full registry first.
  """

  address: Optional[Address] = None
  interfaces: Dict[int, "InterfaceInfo"] = field(default_factory=dict)
  structs: Dict[int, Dict[int, "StructInfo"]] = field(default_factory=dict)
  enums: Dict[int, Dict[int, "EnumInfo"]] = field(default_factory=dict)
  methods: List[MethodInfo] = field(default_factory=list)
  global_pool: Optional["GlobalTypePool"] = None

  def resolve_struct(
    self,
    source_id: int,
    ref_id: int,
    *,
    ho_interface_id: Optional[int] = None,
  ) -> Optional["StructInfo"]:
    """Look up a struct by source_id and ref_id.

    source_id=1: Global pool (1-based ref_id; see GlobalTypePool.resolve_struct).
    source_id=2: Local structs (1-based ref_id -> 0-based struct_id in
      ``self.structs[ho_interface_id]``). ``ho_interface_id`` is required for
      deterministic interface-scoped resolution.
    """
    if source_id == 1 and self.global_pool is not None:
      return self.global_pool.resolve_struct(ref_id)
    if source_id == 2:
      idx = ref_id - 1
      if idx < 0:
        return None
      if ho_interface_id is None:
        return None
      return self.structs.get(ho_interface_id, {}).get(idx)
    if source_id == 3:
      return _NETWORK_STRUCTS.get(ref_id)
    logger.warning("resolve_struct: unhandled source_id=%d ref_id=%d", source_id, ref_id)
    return None

  def resolve_enum(
    self,
    source_id: int,
    ref_id: int,
    *,
    ho_interface_id: Optional[int] = None,
  ) -> Optional["EnumInfo"]:
    """Look up an enum by source_id and ref_id.

    source_id=1: Global pool (1-based ref_id).
    source_id=2: Local enums (same rules as resolve_struct). ``ho_interface_id`` is
      required for strict interface-scoped resolution.
    """
    if source_id == 1 and self.global_pool is not None:
      return self.global_pool.resolve_enum(ref_id)
    if source_id == 2:
      idx = ref_id - 1
      if idx < 0:
        return None
      if ho_interface_id is None:
        return None
      return self.enums.get(ho_interface_id, {}).get(idx)
    return self.enums.get(source_id, {}).get(ref_id)

  def get_method(self, interface_id: int, method_id: int) -> Optional[MethodInfo]:
    """Find a method by interface_id and method_id."""
    for m in self.methods:
      if m.interface_id == interface_id and m.method_id == method_id:
        return m
    return None

  def get_interface_ids(self) -> Set[int]:
    """Return the set of interface IDs this object implements."""
    return set(self.interfaces.keys())

  def to_dict(self) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    addr = (
      f"{self.address.module}:{self.address.node}:{self.address.object}" if self.address else None
    )
    structs_out: Dict[int, List[dict[str, Any]]] = {}
    for iid, struct_table in sorted(self.structs.items()):
      structs_out[iid] = [s.to_dict(self) for _, s in sorted(struct_table.items())]
    enums_out: Dict[int, List[dict[str, Any]]] = {}
    for iid, enum_table in sorted(self.enums.items()):
      enums_out[iid] = [e.to_dict() for _, e in sorted(enum_table.items())]
    return {
      "address": addr,
      "interfaces": [info.to_dict() for _, info in sorted(self.interfaces.items())],
      "methods": [m.to_dict(self) for m in self.methods],
      "structs": structs_out,
      "enums": enums_out,
    }

  def print_summary(self) -> None:
    """Print a summary of all interfaces, structs, enums, and methods."""
    print(f"TypeRegistry for {self.address}")
    print(f"  Interfaces: {sorted(self.interfaces.keys())}")
    for iid, iface in sorted(self.interfaces.items()):
      n_structs = len(self.structs.get(iid, {}))
      n_enums = len(self.enums.get(iid, {}))
      n_methods = sum(1 for m in self.methods if m.interface_id == iid)
      print(f"  [{iid}] {iface.name}: {n_structs} structs, {n_enums} enums, {n_methods} methods")
      for sid, s in sorted(self.structs.get(iid, {}).items()):
        print(f"    struct {sid}: {s.name} ({len(s.fields)} fields)")
      for eid, e in sorted(self.enums.get(iid, {}).items()):
        print(f"    enum {eid}: {e.name} ({len(e.values)} values)")


@dataclass
class InterfaceInfo:
  """Interface metadata from introspection."""

  interface_id: int
  name: str
  version: str

  def to_dict(self) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    return {"interface_id": self.interface_id, "name": self.name, "version": self.version}


@dataclass
class EnumInfo:
  """Enum definition from introspection."""

  enum_id: int
  name: str
  values: Dict[str, int]

  def to_dict(self) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    return {"name": self.name, "enum_id": self.enum_id, "values": dict(self.values)}


@dataclass
class StructInfo:
  """Struct definition from introspection.

  ``interface_id`` records which interface this struct was defined on,
  enabling ``source_id=0`` (same-interface) resolution in the global pool.

  ``fields`` maps field names to ``StructFieldType`` instances, preserving the
  full (type_id, source_id, ref_id) triple for fields that are complex
  references (STRUCTURE/ENUM).  Call ``get_struct_string(registry)``
  to get human-readable names with struct/enum references resolved.
  """

  struct_id: int
  name: str
  fields: Dict[str, "StructFieldType"]  # field_name -> StructFieldType
  interface_id: Optional[int] = None  # Interface this struct was defined on

  @property
  def field_type_names(self) -> Dict[str, str]:
    """Get human-readable field type names using HamiltonDataType resolver."""
    return {name: sft.resolve_name() for name, sft in self.fields.items()}

  def to_dict(self, registry: Optional["TypeRegistry"] = None) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    ho_iid = self.interface_id
    fields = {
      name: sft.resolve_name(registry, ho_interface_id=ho_iid)
      for name, sft in self.fields.items()
    }
    d: dict = {"name": self.name, "struct_id": self.struct_id, "fields": fields}
    if self.interface_id is not None:
      d["interface_id"] = self.interface_id
    return d

  def get_struct_string(self, registry: Optional["TypeRegistry"] = None) -> str:
    """Get struct definition as a readable string.

    If a TypeRegistry is provided, complex references (struct/enum fields)
    are resolved to their names.
    """
    ho_iid = self.interface_id
    field_strs = [
      f"{name}: {sft.resolve_name(registry, ho_interface_id=ho_iid)}"
      for name, sft in self.fields.items()
    ]
    fields_str = "\n  ".join(field_strs) if field_strs else "  (empty)"
    return f"struct {self.name} {{\n  {fields_str}\n}}"


# Populate known network structs now that StructInfo is defined.
# ref_id=3: DateTime — 7 fields: year(U16), month(U8), day(U8), hour(U8),
# minute(U8), second(U8), millisecond(U16). Wire format confirmed via
# GetDeckCalibration on PipettorCalibration.
_NETWORK_STRUCTS[3] = StructInfo(
  struct_id=3,
  name="DateTime",
  fields={
    "year": StructFieldType(HamiltonDataType.U16),
    "month": StructFieldType(HamiltonDataType.U8),
    "day": StructFieldType(HamiltonDataType.U8),
    "hour": StructFieldType(HamiltonDataType.U8),
    "minute": StructFieldType(HamiltonDataType.U8),
    "second": StructFieldType(HamiltonDataType.U8),
    "millisecond": StructFieldType(HamiltonDataType.U16),
  },
  interface_id=3,
)


@dataclass
class GlobalTypePool:
  """Flat, sequentially-indexed pool of structs/enums from global objects.

  Piglet builds this by walking ``robot.globals`` objects, iterating each
  interface's structs/enums, and inserting them in encounter order.  A
  ``source_id=1`` reference uses ``ref_id`` as a **1-based** index into this
  pool (piglet subtracts 1 for lookup).
  """

  structs: List[StructInfo] = field(default_factory=list)
  enums: List[EnumInfo] = field(default_factory=list)
  interface_structs: Dict[int, Dict[int, StructInfo]] = field(default_factory=dict)

  def resolve_struct(self, ref_id: int) -> Optional[StructInfo]:
    """Look up global struct by 1-based ref_id."""
    idx = ref_id - 1  # 1-based → 0-based
    return self.structs[idx] if 0 <= idx < len(self.structs) else None

  def resolve_struct_local(self, interface_id: int, ref_id: int) -> Optional[StructInfo]:
    """Resolve a source_id=0 struct ref within a specific interface."""
    return self.interface_structs.get(interface_id, {}).get(ref_id)

  def resolve_enum(self, ref_id: int) -> Optional[EnumInfo]:
    """Look up global enum by 1-based ref_id."""
    idx = ref_id - 1
    return self.enums[idx] if 0 <= idx < len(self.enums) else None

  def to_dict(self) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    return {
      "structs": [s.to_dict() for s in self.structs],
      "enums": [e.to_dict() for e in self.enums],
    }

  def print_summary(self) -> None:
    """Print global pool summary."""
    print(f"GlobalTypePool: {len(self.structs)} structs, {len(self.enums)} enums")
    for i, s in enumerate(self.structs):
      print(f"  struct[{i + 1}]: {s.name} ({len(s.fields)} fields)")
    for i, e in enumerate(self.enums):
      print(f"  enum[{i + 1}]: {e.name} ({len(e.values)} values)")


# GetStructs wire format (device sends 4 separate array fragments):
# [0] STRING_ARRAY = struct names (one per struct)
# [1] U32_ARRAY   = numberStructureElements — field count for each struct
# [2] U8_ARRAY    = structureElementTypes — flat field type bytes (variable width)
# [3] STRING_ARRAY = structureElementDescriptions — flat field names
#
# structureElementTypes byte encoding:
#   - Simple types: 1 byte using HamiltonDataType values (40=F32, 23=BOOL, etc.)
#   - Complex references: 3 bytes [sentinel, source_id, ref_id]
#     sentinel=30 for STRUCTURE, sentinel=32 for ENUM (matches piglet)
#   The HamiltonDataType namespace is used here, NOT the introspection type namespace.



# ============================================================================
# INTROSPECTION COMMAND CLASSES
# ============================================================================


class GetObjectCommand(TCPCommand):
  """Get object metadata (command_id=1)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 1
  action_code = 0  # QUERY

  def __init__(self, object_address: Address):
    super().__init__(object_address)

  @dataclass(frozen=True)
  class Response:
    name: Str
    version: Str
    method_count: U32
    subobject_count: U16


class GetMethodCommand(TCPCommand):
  """Get method signature (command_id=2)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 2
  action_code = 0  # QUERY

  def __init__(self, object_address: Address, method_index: int):
    super().__init__(object_address)
    self.method_index = method_index

  def build_parameters(self) -> HoiParams:
    """Build parameters for get_method command."""
    return HoiParams().u32(self.method_index)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_method response."""
    parser = HoiParamsParser(data)

    _, interface_id = parser.parse_next()
    _, call_type = parser.parse_next()
    _, method_id = parser.parse_next()
    _, name = parser.parse_next()

    # The remaining fragments are STRING types containing type IDs as bytes.
    # Complex types (struct/enum refs): 3 bytes [type_id, source_id, ref_id] for source_id 1–3;
    # node-global (source_id=4): variable-length quote-delimited form — see _parse_method_param_types.
    # Labels are comma-separated, one per *logical* parameter (matching MethodParamType count).
    parameter_labels_str = None

    if parser.has_remaining():
      # Fragment 4: parameter_types. Wire type is STRING but payload is binary type IDs;
      # use parse_next_raw() to avoid UTF-8 decode failure on bytes 0x80-0xFF.
      _, flags, _, param_types_payload = parser.parse_next_raw()
      if flags & PADDED_FLAG:
        param_types_payload = (
          param_types_payload[:-1] if param_types_payload else param_types_payload
        )
      param_types_payload = param_types_payload.rstrip(b"\x00")  # STRING null terminator
      all_types = _parse_type_ids(param_types_payload)
    else:
      all_types = []

    if parser.has_remaining():
      _, parameter_labels_str = parser.parse_next()

    all_labels: list[str] = []
    if parameter_labels_str:
      all_labels = [label.strip() for label in parameter_labels_str.split(",") if label.strip()]

    parameter_types: list[MethodParamType] = []
    parameter_labels: list[str] = []
    return_types: list[MethodParamType] = []
    return_labels: list[str] = []

    for i, pt in enumerate(all_types):
      label = all_labels[i] if i < len(all_labels) else None

      if pt.is_argument:
        parameter_types.append(pt)
        if label:
          parameter_labels.append(label)
      elif pt.is_return:
        return_types.append(pt)
        if label:
          return_labels.append(label)
      else:
        raise ValueError(
          f"Unknown HOI wire_type={pt.wire_type!r} direction={pt.direction!r}; "
          "not in _HOI_ID_TO_WIRE — update _HOI_TYPE_ROWS or add an override."
        )

    return {
      "interface_id": interface_id,
      "call_type": call_type,
      "method_id": method_id,
      "name": name,
      "parameter_types": parameter_types,
      "parameter_labels": parameter_labels,
      "return_types": return_types,
      "return_labels": return_labels,
    }


class GetSubobjectAddressCommand(TCPCommand):
  """Get subobject address (command_id=3)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 3
  action_code = 0  # QUERY

  def __init__(self, object_address: Address, subobject_index: int):
    super().__init__(object_address)
    self.subobject_index = subobject_index

  def build_parameters(self) -> HoiParams:
    """Build parameters for get_subobject_address command."""
    return HoiParams().u16(self.subobject_index)

  @dataclass(frozen=True)
  class Response:
    module_id: U16
    node_id: U16
    object_id: U16


class GetInterfacesCommand(TCPCommand):
  """Get available interfaces (command_id=4).

  Firmware signature: InterfaceDescriptors(()) -> interfaceIds: I8_ARRAY, interfaceDescriptors: STRING_ARRAY
  Returns 2 columnar fragments, not count+rows.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 4
  action_code = 0  # QUERY

  def __init__(self, object_address: Address):
    super().__init__(object_address)

  @dataclass(frozen=True)
  class Response:
    interface_ids: I8Array
    interface_names: StrArray


class GetEnumsCommand(TCPCommand):
  """Get enum definitions (command_id=5).

  Firmware signature: EnumInfo(interfaceId) -> enumerationNames: STRING_ARRAY,
    numberEnumerationValues: U32_ARRAY, enumerationValues: I32_ARRAY,
    enumerationValueDescriptions: STRING_ARRAY
  Returns 4 columnar fragments, not count+rows.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 5
  action_code = 0  # QUERY

  def __init__(self, object_address: Address, target_interface_id: int):
    super().__init__(object_address)
    self.target_interface_id = target_interface_id

  def build_parameters(self) -> HoiParams:
    """Build parameters for get_enums command."""
    return HoiParams().u8(self.target_interface_id)

  @dataclass(frozen=True)
  class Response:
    enum_names: StrArray
    value_counts: U32Array
    values: I32Array
    value_names: StrArray


class GetStructsCommand(TCPCommand):
  """Get struct definitions (command_id=6)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 6
  action_code = 0  # QUERY

  def __init__(self, object_address: Address, target_interface_id: int):
    super().__init__(object_address)
    self.target_interface_id = target_interface_id

  def build_parameters(self) -> HoiParams:
    """Build parameters for get_structs command."""
    return HoiParams().u8(self.target_interface_id)

  @dataclass(frozen=True)
  class Response:
    """GetStructs returns 4 fragments: struct names, per-struct field counts, flat field type IDs, flat field names.

    Fragment layout (device signature: StructInfo):
      [0] STRING_ARRAY  = struct names (one per struct)
      [1] U32_ARRAY     = numberStructureElements: field count for each struct (NOT struct IDs)
      [2] U8_ARRAY      = structureElementTypes: flat field type IDs across all structs
      [3] STRING_ARRAY  = structureElementDescriptions: flat field names across all structs
    Struct IDs are positional (0-indexed); the device does not send them explicitly.
    """

    struct_names: StrArray
    field_counts: U32Array
    field_type_ids: U8Array
    field_names: StrArray


# ============================================================================
# INTERFACE 0 METHOD IDS (Object Discovery / Introspection)
# ============================================================================
# Used to guard calls: only call an Interface 0 method if it is in the set
# returned by get_supported_interface0_method_ids (from the object's method table).

GET_OBJECT = 1
GET_METHOD = 2
GET_SUBOBJECT_ADDRESS = 3
GET_INTERFACES = 4
GET_ENUMS = 5
GET_STRUCTS = 6


# ============================================================================
# HIGH-LEVEL INTROSPECTION API
# ============================================================================


class HamiltonIntrospection:
  """High-level API for Hamilton introspection.

  Uses the object's method table (GetMethod) to determine which Interface 0
  methods are supported and only calls those. Interfaces are per-object;
  there is no aggregation from children.

  Dependencies are injected as explicit callables rather than a back-reference
  to the client, avoiding the circular reference and the need for a Protocol shim.
  Prefer :attr:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient.introspection`
  over constructing this class directly from application code.
  """

  def __init__(
    self,
    registry: ObjectRegistry,
    global_object_addresses: list[Address],
    send_discovery_command: Callable,
    send_query: Callable,
  ):
    self._registry = registry
    self._global_object_addresses = global_object_addresses
    self._send_discovery_command = send_discovery_command
    self._send_query = send_query
    # Session caches (invalidated by replacing the HamiltonIntrospection instance, e.g. reconnect).
    self._method_table_by_address: Dict[Address, List[MethodInfo]] = {}
    self._iface_types: Dict[
      Tuple[Address, int], Tuple[Dict[int, StructInfo], Dict[int, EnumInfo]]
    ] = {}
    self._interfaces_by_address: Dict[Address, List[InterfaceInfo]] = {}
    self._hc_result_text_by_addr_iface: Dict[Tuple[Address, int], Dict[int, str]] = {}
    self._supported_i0_by_address: Dict[Address, Set[int]] = {}
    self._global_type_pool_singleton: Optional[GlobalTypePool] = None
    self._firmware_tree_cache: Optional[FirmwareTree] = None

  def clear_session_caches(self) -> None:
    """Drop cached method tables, per-interface structs/enums, and the global type pool."""
    self._method_table_by_address.clear()
    self._iface_types.clear()
    self._interfaces_by_address.clear()
    self._hc_result_text_by_addr_iface.clear()
    self._supported_i0_by_address.clear()
    self._global_type_pool_singleton = None
    self._firmware_tree_cache = None

  def _attach_iface_types_to_registry(
    self, registry: TypeRegistry, addr: Address, iface_id: int
  ) -> None:
    """Copy cached structs/enums for (addr, iface_id) into *registry*."""
    entry = self._iface_types.get((addr, iface_id))
    if entry is not None:
      structs_map, enums_map = entry
      registry.structs[iface_id] = dict(structs_map)
      registry.enums[iface_id] = dict(enums_map)

  async def _ensure_parameter_types_for_signature(
    self,
    addr: Address,
    method: MethodInfo,
    registry: TypeRegistry,
  ) -> None:
    """Load structs/enums needed to resolve *method* signatures (recursive struct walk)."""
    seen_structs: Set[Tuple[int, int]] = set()
    max_nodes = 256

    async def walk(types: List[Union[MethodParamType, StructFieldType]], ho_iface: int) -> None:
      for pt in types:
        if pt.source_id is None or pt.ref_id is None:
          continue
        if pt.source_id in (1, 3):
          continue
        if pt.source_id != 2:
          continue
        if pt.is_enum_ref:
          await self.ensure_structs_enums(addr, ho_iface)
          self._attach_iface_types_to_registry(registry, addr, ho_iface)
          continue
        if pt.is_struct_ref:
          await self.ensure_structs_enums(addr, ho_iface)
          self._attach_iface_types_to_registry(registry, addr, ho_iface)
          st = registry.resolve_struct(2, pt.ref_id, ho_interface_id=ho_iface)
          if st is None:
            continue
          field_iface = st.interface_id if st.interface_id is not None else ho_iface
          sig = (field_iface, st.struct_id)
          if sig in seen_structs:
            continue
          if len(seen_structs) >= max_nodes:
            logger.warning(
              "signature struct walk exceeded %d nodes for %s.%s",
              max_nodes,
              method.name,
              st.name,
            )
            return
          seen_structs.add(sig)
          await walk(list(st.fields.values()), field_iface)

    await walk(method.parameter_types, method.interface_id)
    await walk(method.return_types, method.interface_id)

  async def _build_minimal_registry_for_signature(
    self, addr: Address, method: MethodInfo
  ) -> TypeRegistry:
    """TypeRegistry with global pool + lazily filled local tables for *method*."""
    pool = await self.ensure_global_type_pool()
    registry = TypeRegistry(address=addr, global_pool=pool)
    await self._ensure_parameter_types_for_signature(addr, method, registry)
    return registry

  async def _build_global_type_pool_impl(self, global_addresses: List[Address]) -> GlobalTypePool:
    """Walk global objects and build a :class:`GlobalTypePool` (full firmware-scale pass)."""
    pool = GlobalTypePool()

    for addr in global_addresses:
      try:
        supported = await self.get_supported_interface0_method_ids(addr)
        if GET_INTERFACES not in supported:
          continue

        interfaces = await self.get_interfaces(addr, _supported=supported)
        # source_id=1 refs index into the first non-zero interface's struct/enum list
        # (firmware always resolves via interface_id=1; see HoiObject.HandleStruct).
        # Populate interface_structs for all interfaces, but only extend the flat pool
        # from the first non-zero interface so ref_ids remain valid.
        first_nonzero_seen = False
        for iface in interfaces:
          if iface.interface_id == 0:
            continue
          if GET_STRUCTS in supported:
            structs = await self.get_structs(addr, iface.interface_id)
            pool.interface_structs[iface.interface_id] = {s.struct_id: s for s in structs}
            if not first_nonzero_seen:
              pool.structs.extend(structs)
          if GET_ENUMS in supported:
            enums = await self.get_enums(addr, iface.interface_id)
            if not first_nonzero_seen:
              pool.enums.extend(enums)
          first_nonzero_seen = True
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.warning("build_global_type_pool failed for %s: %s", addr, e)

    logger.info(
      "Global type pool built: %d structs, %d enums from %d global objects",
      len(pool.structs),
      len(pool.enums),
      len(global_addresses),
    )
    return pool

  async def ensure_method_table(
    self,
    address: Union[Address, str],
    *,
    _supported: Optional[Set[int]] = None,
    _object_info: Optional[ObjectInfo] = None,
  ) -> List[MethodInfo]:
    """Scan Interface 0 GetMethod for *address* once and cache the full ``MethodInfo`` table.

    Pass ``_object_info`` / ``_supported`` when the caller already has them to avoid redundant
    Interface-0 queries on the cold path.
    """
    addr = await self._resolve_target_address(address)
    cached = self._method_table_by_address.get(addr)
    if cached is not None:
      return cached
    cached_supported = self._supported_i0_by_address.get(addr)
    if cached_supported is not None and GET_METHOD not in cached_supported:
      self._method_table_by_address[addr] = []
      return []
    if _supported is not None and GET_METHOD not in _supported:
      self._supported_i0_by_address[addr] = set(_supported)
      self._method_table_by_address[addr] = []
      return []
    if _object_info is None:
      _object_info = await self.get_object(addr)
    methods: List[MethodInfo] = []
    for i in range(_object_info.method_count):
      try:
        method = await self.get_method(addr, i)
        methods.append(method)
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.warning("Failed to get method %d for %s: %s", i, addr, e)
    self._method_table_by_address[addr] = methods
    self._supported_i0_by_address[addr] = {m.method_id for m in methods if m.interface_id == 0}
    return methods

  async def methods_for_interface(
    self, address: Union[Address, str], interface_id: int
  ) -> List[MethodInfo]:
    """Return methods for *interface_id* using the cached method table when warm."""
    addr = await self._resolve_target_address(address)
    table = await self.ensure_method_table(addr)
    return [m for m in table if m.interface_id == interface_id]

  async def ensure_structs_enums(self, address: Union[Address, str], interface_id: int) -> None:
    """Run GetStructs/GetEnums for one HO interface and cache under ``(address, interface_id)``."""
    addr = await self._resolve_target_address(address)
    key = (addr, interface_id)
    if key in self._iface_types:
      return
    supported = await self.get_supported_interface0_method_ids(addr)
    structs_map: Dict[int, StructInfo] = {}
    enums_map: Dict[int, EnumInfo] = {}
    if GET_STRUCTS in supported:
      structs = await self.get_structs(addr, interface_id)
      structs_map = {s.struct_id: s for s in structs}
    if GET_ENUMS in supported:
      enums = await self.get_enums(addr, interface_id)
      enums_map = {e.enum_id: e for e in enums}
    self._iface_types[key] = (structs_map, enums_map)
    hc_result = next((e for e in enums_map.values() if e.name == "HcResult"), None)
    if hc_result is not None:
      self._hc_result_text_by_addr_iface[key] = {int(v): n for n, v in hc_result.values.items()}
    else:
      self._hc_result_text_by_addr_iface[key] = {}

  async def get_interface_name(
    self, address: Union[Address, str], interface_id: int
  ) -> Optional[str]:
    """Return interface name for ``(address, interface_id)`` using session cache."""
    addr = await self._resolve_target_address(address)
    infos = self._interfaces_by_address.get(addr)
    if infos is None:
      infos = await self.get_interfaces(addr)
      self._interfaces_by_address[addr] = infos
    for info in infos:
      if info.interface_id == interface_id:
        return info.name
    return None

  async def get_hc_result_text(
    self, address: Union[Address, str], interface_id: int, code: int
  ) -> Optional[str]:
    """Resolve HcResult enum text for one interface using cached enums."""
    addr = await self._resolve_target_address(address)
    key = (addr, interface_id)
    if key not in self._iface_types:
      await self.ensure_structs_enums(addr, interface_id)
    return self._hc_result_text_by_addr_iface.get(key, {}).get(code)

  async def ensure_global_type_pool(
    self, global_addresses: Optional[Sequence[Address]] = None
  ) -> GlobalTypePool:
    """Return the session-global :class:`GlobalTypePool` (``source_id=1``), building once."""
    if self._global_type_pool_singleton is not None:
      return self._global_type_pool_singleton
    addrs = (
      list(global_addresses)
      if global_addresses is not None
      else list(self._global_object_addresses)
    )
    self._global_type_pool_singleton = await self._build_global_type_pool_impl(addrs)
    return self._global_type_pool_singleton

  async def signature_lines_for_interface(
    self,
    address: Union[Address, str],
    interface_id: int,
    *,
    max_methods: int = 50,
  ) -> List[str]:
    """Resolved signature strings for up to *max_methods* methods on *interface_id* (lazy types)."""
    addr = await self._resolve_target_address(address)
    methods = [m for m in await self.ensure_method_table(addr) if m.interface_id == interface_id][
      :max_methods
    ]
    lines: List[str] = []
    for m in methods:
      reg = await self._build_minimal_registry_for_signature(addr, m)
      lines.append(m.get_signature_string(reg))
    return lines

  async def _resolve_target_address(self, addr_or_path: Union[Address, str]) -> Address:
    """Resolve Address or dot-path to Address."""
    if isinstance(addr_or_path, str):
      return await self.resolve_path(addr_or_path)
    return addr_or_path

  async def _walk_node(
    self, addr: Address, path: Optional[str], visited: Set[Address]
  ) -> Optional[FirmwareTreeNode]:
    """Walk one firmware object node, register it, and recurse into children.

    Used by :meth:`_build_firmware_tree` for a full eager DFS.
    Pass ``path=None`` to derive the path from the object's own name (root nodes).
    Returns ``None`` if *addr* was already visited.
    """
    if addr in visited:
      return None
    visited.add(addr)

    obj = await self.get_object(addr)
    if path is None:
      path = obj.name
    supported = await self.get_supported_interface0_method_ids(addr)
    node = FirmwareTreeNode(
      path=path,
      address=addr,
      object_info=obj,
      supported_interface0_methods=supported,
    )
    self._registry.register(path, obj)

    # Keep this guard even though Interface-0 method 3 (GetSubobjectAddress)
    # appears ubiquitous in current PREP captures.
    if GET_SUBOBJECT_ADDRESS not in supported:
      return node

    for i in range(obj.subobject_count):
      try:
        sub_addr, sub_obj = await _subobject_address_and_info(self, addr, i)
        obj.children[sub_obj.name] = sub_obj
        child = await self._walk_node(sub_addr, f"{path}.{sub_obj.name}", visited)
        if child is not None:
          node.children.append(child)
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.debug("walk child failed for %s idx=%d: %s", addr, i, e)
    return node

  async def resolve_path(self, path: str) -> Address:
    """Resolve a dot-path (e.g. ``"MLPrepRoot.MphRoot.MPH"``) to an :class:`Address`.

    Checks the registry cache first. On a miss, resolves one segment at a time —
    enumerating only the children needed at each level — so deep paths on large
    firmware trees do not trigger a full tree walk.
    Raises :exc:`KeyError` if the path cannot be found.
    """
    cached = self._registry.address_for(path)
    if cached is not None:
      return cached

    parts = [p for p in path.split(".") if p]
    if not parts:
      raise KeyError(f"Invalid path: '{path}'")

    root_addr = self._registry.get_root_address()
    if root_addr is None:
      raise KeyError(f"No root address registered; cannot resolve path '{path}'")

    root_obj = await self.get_object(root_addr)
    self._registry.register(root_obj.name, root_obj)
    if root_obj.name != parts[0]:
      raise KeyError(f"Root object is '{root_obj.name}', not '{parts[0]}'")
    if len(parts) == 1:
      return root_addr

    current_addr = root_addr
    current_path = parts[0]
    for part in parts[1:]:
      next_path = f"{current_path}.{part}"
      cached = self._registry.address_for(next_path)
      if cached is not None:
        current_addr = cached
        current_path = next_path
        continue

      obj = await self.get_object(current_addr)
      supported = await self.get_supported_interface0_method_ids(current_addr)
      if GET_SUBOBJECT_ADDRESS not in supported:
        raise KeyError(
          f"'{current_path}' does not support GetSubobjectAddress; cannot resolve child '{part}'"
        )

      found: Optional[Address] = None
      for i in range(obj.subobject_count):
        sub_addr, sub_obj = await _subobject_address_and_info(self, current_addr, i)
        self._registry.register(f"{current_path}.{sub_obj.name}", sub_obj)
        if sub_obj.name == part:
          found = sub_addr

      if found is None:
        raise KeyError(f"Child '{part}' not found under '{current_path}'")
      current_addr = found
      current_path = next_path

    return current_addr

  async def _build_firmware_tree(self) -> FirmwareTree:
    """Build a DFS firmware tree from the single registered root address."""
    root_addr = self._registry.get_root_address()
    if root_addr is None:
      raise RuntimeError("Cannot build firmware tree: no root address registered")

    visited: Set[Address] = set()
    node = await self._walk_node(root_addr, None, visited)
    if node is None:
      raise RuntimeError(f"Root node walk returned None for address {root_addr}")
    return FirmwareTree(root=node)

  async def get_firmware_tree(self, refresh: bool = False) -> FirmwareTree:
    """Return cached firmware tree, or build and cache it when missing."""
    if not refresh and self._firmware_tree_cache is not None:
      return self._firmware_tree_cache

    self._firmware_tree_cache = await self._build_firmware_tree()
    return self._firmware_tree_cache

  async def get_firmware_tree_flat(
    self, refresh: bool = False
  ) -> List[Tuple[str, Address, ObjectInfo]]:
    """Firmware tree as a flat preorder list of ``(path, address, object_info)``."""
    tree = await self.get_firmware_tree(refresh=refresh)
    return flatten_firmware_tree(tree)

  async def get_supported_interface0_method_ids(self, address: Address) -> Set[int]:
    """Return the set of Interface 0 method IDs this object supports.

    Calls GetObject to get method_count, then GetMethod(address, i) for each
    index and collects method_id for every method where interface_id == 0.
    Used to guard calls so we never send an Interface 0 command the object
    did not advertise.
    """
    cached = self._supported_i0_by_address.get(address)
    if cached is not None:
      return set(cached)

    methods = self._method_table_by_address.get(address)
    if methods is None:
      obj = await self.get_object(address)
      methods = await self.ensure_method_table(address, _object_info=obj)
    supported = {m.method_id for m in methods if m.interface_id == 0}
    self._supported_i0_by_address[address] = set(supported)
    return set(supported)

  async def get_object(self, address: Address) -> ObjectInfo:
    """Get object metadata.

    Args:
      address: Object address to query

    Returns:
      Object metadata
    """
    command = GetObjectCommand(address)
    response = await self._send_discovery_command(command)
    if response is None:
      raise RuntimeError("GetObjectCommand returned None")

    return ObjectInfo(
      name=response.name,
      version=response.version,
      method_count=int(response.method_count),
      subobject_count=int(response.subobject_count),
      address=address,
    )

  async def get_method(self, address: Address, method_index: int) -> MethodInfo:
    """Get method signature.

    Args:
      address: Object address
      method_index: Method index to query

    Returns:
      Method signature
    """
    command = GetMethodCommand(address, method_index)
    response = await self._send_discovery_command(command)

    return MethodInfo(
      interface_id=response["interface_id"],
      call_type=response["call_type"],
      method_id=response["method_id"],
      name=response["name"],
      parameter_types=response.get("parameter_types", []),
      parameter_labels=response.get("parameter_labels", []),
      return_types=response.get("return_types", []),
      return_labels=response.get("return_labels", []),
    )

  async def get_subobject_address(self, address: Address, subobject_index: int) -> Address:
    """Get subobject address.

    Args:
      address: Parent object address
      subobject_index: Subobject index

    Returns:
      Subobject address
    """
    command = GetSubobjectAddressCommand(address, subobject_index)
    response = await self._send_discovery_command(command)
    if response is None:
      raise RuntimeError("GetSubobjectAddressCommand returned None")

    return Address(response.module_id, response.node_id, response.object_id)

  async def get_interfaces(
    self,
    address: Address,
    *,
    _supported: Optional[Set[int]] = None,
  ) -> List[InterfaceInfo]:
    """Get available interfaces.

    The device returns 2 columnar fragments: interface_ids (I8_ARRAY) and
    interface_names (STRING_ARRAY). Returns [] if the object does not support
    GetInterfaces (interface 0, method 4).

    Args:
      address: Object address
      _supported: Pre-computed supported Interface 0 method IDs (internal;
        avoids redundant device queries when the caller already has them).

    Returns:
      List of interface information
    """
    if _supported is None:
      _supported = await self.get_supported_interface0_method_ids(address)
    if GET_INTERFACES not in _supported:
      logger.debug(
        "Object at %s does not support GetInterfaces (interface 0, method 4); returning []",
        address,
      )
      return []
    command = GetInterfacesCommand(address)
    response = await self._send_discovery_command(command)
    if response is None:
      raise RuntimeError("GetInterfacesCommand returned None")

    ids = list(response.interface_ids)
    names = list(response.interface_names)
    infos = [
      InterfaceInfo(
        interface_id=int(ids[i]),
        name=names[i] if i < len(names) else f"Interface_{ids[i]}",
        version="",
      )
      for i in range(len(ids))
    ]
    self._interfaces_by_address[address] = infos
    return infos

  async def get_enums(self, address: Address, interface_id: int) -> List[EnumInfo]:
    """Get enum definitions.

    The device returns 4 columnar fragments: enum_names (STRING_ARRAY),
    value_counts (U32_ARRAY), values (I32_ARRAY), value_names (STRING_ARRAY).
    Values/names are split across enums using the value_counts.

    Args:
      address: Object address
      interface_id: Interface ID

    Returns:
      List of enum definitions
    """
    command = GetEnumsCommand(address, interface_id)
    response = await self._send_discovery_command(command)
    if response is None:
      raise RuntimeError("GetEnumsCommand returned None")

    enum_names = list(response.enum_names)
    value_counts = list(response.value_counts)
    all_values = list(response.values)
    all_value_names = list(response.value_names)
    n_enums = len(enum_names)
    if n_enums == 0:
      return []
    offset = 0
    result: List[EnumInfo] = []
    for i in range(n_enums):
      cnt = int(value_counts[i]) if i < len(value_counts) else 0
      names_slice = all_value_names[offset : offset + cnt]
      values_slice = all_values[offset : offset + cnt]
      vals = dict(zip(names_slice, values_slice))
      result.append(EnumInfo(enum_id=i, name=enum_names[i], values=vals))
      offset += cnt
    return result

  async def _get_structs_raw(self, address: Address, interface_id: int) -> tuple[bytes, List[dict]]:
    """Get raw GetStructs response bytes and a fragment-by-fragment breakdown.

    Use this to see exactly what the device sends so response parsing can
    match the wire format. Returns (params_bytes, inspect_hoi_params(params)).

    Example:
      raw, fragments = await intro.get_structs_raw(mph_addr, 1)
      for i, f in enumerate(fragments):
        print(f\"{i}: type_id={f['type_id']} len={f['length']} decoded={f['decoded']!r}\")
    """
    command = GetStructsCommand(address, interface_id)
    result = await self._send_query(command)
    if result is None:
      raise RuntimeError("GetStructs query returned no data.")
    (params,) = result
    return params, inspect_hoi_params(params)

  async def get_structs(self, address: Address, interface_id: int) -> List[StructInfo]:
    """Get struct definitions.

    The device returns 4 fragments per the StructInfo signature:
      [0] struct_names (StrArray): one name per struct
      [1] field_counts (U32Array): numberStructureElements — how many fields each struct has
      [2] field_type_ids (U8Array): flat field type IDs across all structs
      [3] field_names (StrArray): flat field names across all structs

    Struct IDs are positional (0-indexed); the device does not send them explicitly.
    field_counts drives the field-to-struct assignment (no even-split heuristic).

    Args:
      address: Object address
      interface_id: Interface ID

    Returns:
      List of struct definitions
    """
    command = GetStructsCommand(address, interface_id)
    response = await self._send_discovery_command(command)
    if response is None:
      raise RuntimeError("GetStructsCommand returned None")

    struct_names = list(response.struct_names)
    # field_counts = numberStructureElements from the device: logical fields per struct.
    # Struct IDs are positional (0-indexed); the device does not send them.
    field_counts = [int(c) for c in response.field_counts]
    type_bytes = list(response.field_type_ids)  # flat byte array; entries are 1, 3, or 7 bytes wide
    field_names = list(response.field_names)
    n_structs = len(field_counts)
    if n_structs == 0:
      return []

    # Walk type_bytes with a byte-level cursor. Width varies: 1=simple, 3=ref (source_id 1–3),
    # 7=node-global (source_id=4). field_counts gives logical field count per struct,
    # not bytes — _parse_struct_field_types tracks exact byte consumption via _byte_width.
    byte_offset = 0  # cursor into type_bytes
    name_offset = 0  # cursor into field_names
    result: List[StructInfo] = []
    for i, cnt in enumerate(field_counts):
      name = struct_names[i] if i < len(struct_names) else f"Struct_{i}"
      parsed = _parse_struct_field_types(type_bytes[byte_offset:])
      # Consume exactly `cnt` logical entries; advance byte_offset by the bytes used.
      type_entries = parsed[:cnt]
      bytes_used = sum(pt._byte_width for pt in type_entries)
      names_slice = field_names[name_offset : name_offset + cnt]
      fields = dict(zip(names_slice, type_entries))
      result.append(StructInfo(struct_id=i, name=name, fields=fields, interface_id=interface_id))
      byte_offset += bytes_used
      name_offset += cnt
    return result

  async def build_type_registry(
    self,
    address: Union[Address, str],
    global_pool: Optional[GlobalTypePool] = None,
    *,
    _supported: Optional[Set[int]] = None,
  ) -> TypeRegistry:
    """Build a complete TypeRegistry for an object.

    Uses InterfaceDescriptors (get_interfaces) as the canonical source of
    interface IDs; then queries structs and enums only for those interfaces.
    Only calls Interface 0 methods that the object supports; skips unsupported
    commands and builds a partial registry.

    Args:
      address: Object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").
      global_pool: Optional GlobalTypePool for resolving source_id=1 refs. If omitted,
        uses :meth:`ensure_global_type_pool` (same as :meth:`_build_minimal_registry_for_signature`).
      _supported: Pre-computed supported Interface 0 method IDs (internal;
        avoids redundant device queries when the caller already has them).

    Returns:
      TypeRegistry with all type information for this object
    """
    address = await self._resolve_target_address(address)
    if global_pool is None:
      global_pool = await self.ensure_global_type_pool()
    registry = TypeRegistry(address=address, global_pool=global_pool)
    if _supported is None:
      _supported = await self.get_supported_interface0_method_ids(address)

    if GET_INTERFACES in _supported:
      interfaces = await self.get_interfaces(address, _supported=_supported)
      for iface in interfaces:
        registry.interfaces[iface.interface_id] = iface
    else:
      interfaces = []

    if GET_METHOD in _supported:
      registry.methods = await self.ensure_method_table(address)
    else:
      registry.methods = []

    for iface in interfaces:
      if GET_STRUCTS in _supported or GET_ENUMS in _supported:
        await self.ensure_structs_enums(address, iface.interface_id)
        self._attach_iface_types_to_registry(registry, address, iface.interface_id)

    return registry

  async def build_type_registry_with_children(
    self,
    address: Union[Address, str],
    subobject_addresses: Optional[List[Address]] = None,
    global_pool: Optional[GlobalTypePool] = None,
  ) -> TypeRegistry:
    """Build a TypeRegistry that includes structs/enums from child objects.

    Complex type references (e.g. type_57 = PickupTipParameters) may be
    defined on a child object's interface rather than the parent. This method
    builds the parent's registry, then merges in types from each child so
    that MethodParamType.resolve_name() can find them.

    Args:
      address: Parent object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").
      subobject_addresses: Optional list of child addresses to include.
        If None, all direct subobjects are discovered automatically.
      global_pool: Optional GlobalTypePool for resolving source_id=1 refs. If omitted,
        :meth:`build_type_registry` attaches the session pool automatically.

    Returns:
      TypeRegistry that can resolve types from both parent and children.
    """
    address = await self._resolve_target_address(address)
    supported = await self.get_supported_interface0_method_ids(address)
    registry = await self.build_type_registry(
      address, global_pool=global_pool, _supported=supported
    )

    if subobject_addresses is None:
      if GET_SUBOBJECT_ADDRESS not in supported:
        subobject_addresses = []
      else:
        obj_info = await self.get_object(address)
        subobject_addresses = []
        for i in range(obj_info.subobject_count):
          try:
            sub_addr = await self.get_subobject_address(address, i)
            subobject_addresses.append(sub_addr)
          except _TRANSIENT_ERRORS:
            raise
          except Exception:
            logger.debug("get_subobject_address(%d) failed for %s", i, address)

    for sub_addr in subobject_addresses:
      try:
        child_reg = await self.build_type_registry(sub_addr)
        for iid, struct_map in child_reg.structs.items():
          registry.structs.setdefault(iid, {}).update(struct_map)
        for iid, enum_map in child_reg.enums.items():
          registry.enums.setdefault(iid, {}).update(enum_map)
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.debug("build_type_registry failed for child %s: %s", sub_addr, e)

    return registry

  async def build_global_type_pool(
    self,
    global_addresses: List[Address],
  ) -> GlobalTypePool:
    """Build a fresh global type pool from *global_addresses* (full walk; not the session singleton).

    Mirrors piglet: walk each global object, iterate interfaces, collect structs/enums in
    encounter order for ``source_id=1`` lookups. For lazy signature resolution on a live
    session, use :meth:`ensure_global_type_pool` so the pool is built once and reused.

    Args:
      global_addresses: List of global object addresses
        (from :attr:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient.global_object_addresses`).

    Returns:
      GlobalTypePool with all global structs and enums.
    """
    return await self._build_global_type_pool_impl(global_addresses)

  async def get_method_by_id(
    self,
    address: Union[Address, str],
    interface_id: int,
    method_id: int,
    registry: Optional[TypeRegistry] = None,
  ) -> Optional[MethodInfo]:
    """Return the method with the given interface_id and method_id (action id).

    When a TypeRegistry is provided and contains the method, returns it
    without any device round-trips. Falls back to a full device scan only
    when no registry is available or the method isn't in it.

    Args:
      address: Object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").
      interface_id: Interface ID (e.g. 1 for IChannel/IMph).
      method_id: Method/command ID (e.g. 9 for PickupTips).
      registry: Optional TypeRegistry with cached methods.

    Returns:
      MethodInfo for the matching method, or None if not found.
    """
    if registry is not None:
      cached = registry.get_method(interface_id, method_id)
      if cached is not None:
        return cached
    address = await self._resolve_target_address(address)
    methods = await self.ensure_method_table(address)
    for m in methods:
      if m.interface_id == interface_id and m.method_id == method_id:
        return m
    return None

  async def resolve_signature(
    self,
    address: Union[Address, str],
    interface_id: int,
    method_id: int,
    registry: Optional[TypeRegistry] = None,
  ) -> str:
    """Return a fully resolved method signature string.

    When *registry* is omitted, loads only the
    method table, global pool, and structs/enums needed for this signature (no full
    :meth:`build_type_registry`). Pass an explicit *registry* for export/golden parity.

    Example::

      sig = await intro.resolve_signature("MLPrepRoot.MphRoot.MPH", 1, 9)
      print(sig)
      # PickupTips(tipParameters: PickupTipParameters, finalZ: f32, ...) -> ...

    Returns:
      Human-readable signature string, or a descriptive error string.
    """
    address = await self._resolve_target_address(address)
    if registry is not None:
      method = await self.get_method_by_id(address, interface_id, method_id, registry=registry)
      if method is None:
        return f"<method not found: iface={interface_id} id={method_id} at {address}>"
      return method.get_signature_string(registry)
    methods = await self.ensure_method_table(address)
    method = next(
      (m for m in methods if m.interface_id == interface_id and m.method_id == method_id),
      None,
    )
    if method is None:
      return f"<method not found: iface={interface_id} id={method_id} at {address}>"
    reg = await self._build_minimal_registry_for_signature(address, method)
    return method.get_signature_string(reg)
