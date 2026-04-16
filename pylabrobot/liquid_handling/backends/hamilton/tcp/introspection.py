"""Hamilton TCP Introspection API.

Wraps HamiltonTCPClient to provide dynamic discovery of instrument capabilities
via Interface 0 methods (GetObject, GetMethod, GetStructs, GetEnums,
GetInterfaces, GetSubobjectAddress).

Canonical usage::

    intro = HamiltonIntrospection(client)                    # standalone
    intro = HamiltonIntrospection(lh.backend.client)         # from LiquidHandler

    # Build a cached registry for one object (uses InterfaceDescriptors):
    registry = await intro.build_type_registry("MLPrepRoot.MphRoot.MPH")
    registry.print_summary()

    # Resolve a method signature:
    sig = await intro.resolve_signature("MLPrepRoot.MphRoot.MPH", 1, 9, registry)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, TypeVar, Union, cast

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  PADDED_FLAG,
  HoiParams,
  HoiParamsParser,
  inspect_hoi_params,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  U8,
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
    return HamiltonDataType(type_id).name
  except ValueError:
    return f"UNKNOWN_TYPE_{type_id}"


# ============================================================================
# INTROSPECTION TYPE MAPPING (2D table from HoiObject.mHoiParamTypes)
# ============================================================================
# Introspection type IDs are separate from HamiltonDataType wire encoding types.
# Rows = firmware scalar or array kinds; columns = In, Out, InOut, RetVal
# (HoiParameterType.Direction). Source: vendor protocol reference mHoiParamTypes[31,4].

_HOI_DOTNET_TYPE_ROWS: tuple[str, ...] = (
  "i8",
  "i16",
  "i32",
  "u8",
  "u16",
  "u32",
  "str",
  "bool",
  "i8[]",
  "i16[]",
  "i32[]",
  "u8[]",
  "u16[]",
  "u32[]",
  "bool[]",
  "HcResult",
  "struct",
  "struct[]",
  "str[]",
  "enum",
  "enum[]",
  "i64",
  "u64",
  "f32",
  "f64",
  "i64[]",
  "u64[]",
  "f32[]",
  "f64[]",
  "HoiResult",
  "padding",
)

_HOI_PARAM_DIRECTION: tuple[str, ...] = ("In", "Out", "InOut", "RetVal")

# type_ids per row [In, Out, InOut, RetVal] — ord() of each C# string cell (row 30 unused).
_HOI_PARAM_TYPE_GRID: tuple[tuple[int, int, int, int], ...] = (
  (1, 17, 9, 25),
  (3, 19, 11, 27),
  (5, 21, 13, 29),
  (2, 18, 10, 26),
  (4, 20, 12, 28),
  (6, 22, 14, 30),
  (7, 23, 15, 31),
  (33, 35, 34, 36),
  (37, 39, 38, 40),
  (41, 43, 42, 44),
  (49, 51, 50, 52),
  (8, 24, 16, 32),
  (45, 47, 46, 48),
  (53, 55, 54, 56),
  (66, 68, 67, 69),
  (70, 72, 71, 73),
  (57, 59, 58, 60),
  (61, 63, 62, 64),
  (74, 76, 75, 77),
  (78, 80, 79, 81),
  (82, 84, 83, 85),
  (86, 88, 87, 89),
  (90, 92, 91, 93),
  (94, 96, 95, 97),
  (98, 100, 99, 101),
  (102, 104, 103, 105),
  (106, 108, 107, 109),
  (110, 112, 111, 113),
  (114, 116, 115, 117),
  (118, 120, 119, 121),
  (0, 0, 0, 0),
)

_ROW_DISPLAY: dict[str, str] = {
  "i8": "i8",
  "i16": "i16",
  "i32": "i32",
  "u8": "u8",
  "u16": "u16",
  "u32": "u32",
  "str": "str",
  "bool": "bool",
  "i8[]": "List[i8]",
  "i16[]": "List[i16]",
  "i32[]": "List[i32]",
  "u8[]": "bytes",
  "u16[]": "List[u16]",
  "u32[]": "List[u32]",
  "bool[]": "List[bool]",
  "HcResult": "HcResult",
  "struct": "struct",
  "struct[]": "List[struct]",
  "str[]": "List[str]",
  "enum": "enum",
  "enum[]": "List[enum]",
  "i64": "i64",
  "u64": "u64",
  "f32": "f32",
  "f64": "f64",
  "i64[]": "List[i64]",
  "u64[]": "List[u64]",
  "f32[]": "List[f32]",
  "f64[]": "List[f64]",
  "HoiResult": "HoiResult",
  "padding": "padding",
}


def _build_introspection_maps() -> tuple[dict[int, str], set[int], set[int], set[int], set[int]]:
  names: dict[int, str] = {0: "void"}
  arg_ids: set[int] = set()
  ret_el_ids: set[int] = set()
  ret_val_ids: set[int] = set()
  complex_method_ids: set[int] = set()
  complex_rows = frozenset({15, 16, 17, 18, 19, 20, 29})

  for ri, row in enumerate(_HOI_PARAM_TYPE_GRID):
    base_key = _HOI_DOTNET_TYPE_ROWS[ri]
    for ci, tid in enumerate(row):
      if tid == 0:
        continue
      d = _HOI_PARAM_DIRECTION[ci]
      disp = _ROW_DISPLAY.get(base_key, base_key)
      names[tid] = f"{disp} [{d}]"
      if ci in (0, 2):
        arg_ids.add(tid)
      elif ci == 1:
        ret_el_ids.add(tid)
      elif ci == 3:
        ret_val_ids.add(tid)
      if ri in complex_rows:
        complex_method_ids.add(tid)

  return names, arg_ids, ret_el_ids, ret_val_ids, complex_method_ids


(
  _INTROSPECTION_TYPE_NAMES,
  _ARGUMENT_TYPE_IDS,
  _RETURN_ELEMENT_TYPE_IDS,
  _RETURN_VALUE_TYPE_IDS,
  _COMPLEX_METHOD_TYPE_IDS,
) = _build_introspection_maps()

# Empirical / device-specific id observed in the wild (not in HoiObject 31×4 grid).
_INTROSPECTION_TYPE_NAMES[113] = "List[f32] [In] (empirical)"
_ARGUMENT_TYPE_IDS.add(113)

_COMPLEX_STRUCT_TYPE_IDS = {30, 31, 32, 35}  # STRUCTURE=30, STRUCT_ARRAY=31, ENUM=32, ENUM_ARRAY=35
# Backward-compat alias (used by ParameterType.is_complex for method parameters)
_COMPLEX_TYPE_IDS = _COMPLEX_METHOD_TYPE_IDS


def get_introspection_type_category(type_id: int) -> str:
  """Get category for introspection type ID.

  Args:
      type_id: Introspection type ID

  Returns:
      Category: "Argument", "ReturnElement", "ReturnValue", or "Unknown"
  """
  if type_id in _ARGUMENT_TYPE_IDS:
    return "Argument"
  elif type_id in _RETURN_ELEMENT_TYPE_IDS:
    return "ReturnElement"
  elif type_id in _RETURN_VALUE_TYPE_IDS:
    return "ReturnValue"
  else:
    return "Unknown"


def resolve_introspection_type_name(type_id: int) -> str:
  """Resolve introspection type ID to readable name.

  Args:
      type_id: Introspection type ID

  Returns:
      Human-readable type name
  """
  return _INTROSPECTION_TYPE_NAMES.get(type_id, f"UNKNOWN_TYPE_{type_id}")


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


@dataclass
class ParameterType:
  """A resolved type reference used for both method parameters and struct fields.

  Simple types (i8, f32, etc.) have only type_id set.
  Complex references additionally carry source_id (the interface defining the
  struct/enum) and ref_id (struct_id or enum_id within that interface).
  These are encoded as 3-byte triples [type_id, source_id, ref_id] in two
  distinct contexts that each use a different sentinel byte:

  - GetMethod parameterTypes: sentinels in _COMPLEX_METHOD_TYPE_IDS (57, 61 …)
  - GetStructs structureElementTypes: sentinel 0xE8 (_COMPLEX_STRUCT_TYPE_IDS)
  """

  type_id: int
  source_id: Optional[int] = None
  ref_id: Optional[int] = None
  _byte_width: int = 1  # Bytes consumed in struct element_types (1=simple, 3=ref, 7+=inline)

  @property
  def is_complex(self) -> bool:
    """True if this is a 3-byte complex reference (method param or struct field)."""
    return self.type_id in (_COMPLEX_METHOD_TYPE_IDS | _COMPLEX_STRUCT_TYPE_IDS)

  @property
  def is_struct_ref(self) -> bool:
    """True if this is a struct reference (type 30 in struct context, 57/61 in method context)."""
    return self.type_id in {30, 31, 57, 60, 61, 63, 64}

  @property
  def is_enum_ref(self) -> bool:
    """True if this is an enum reference (type 32 in struct context, 78/81/82/85 in method)."""
    return self.type_id in {32, 35, 78, 81, 82, 85}

  def resolve_name(
    self,
    registry: Optional["TypeRegistry"] = None,
    ho_interface_id: Optional[int] = None,
  ) -> str:
    """Resolve to a human-readable name, optionally using a TypeRegistry.

    For source_id=2 (local) refs, pass ``ho_interface_id`` (the HOI interface id
    of the method or struct owning this type) so resolution uses that interface's
    table only. If omitted, registry falls back to multi-interface heuristics.
    """
    base = resolve_introspection_type_name(self.type_id)
    if not self.is_complex or self.source_id is None or self.ref_id is None:
      return base
    if registry is None:
      return f"{base}(iface={self.source_id}, id={self.ref_id})"
    if self.is_struct_ref:
      s = registry.resolve_struct(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
      return s.name if s else f"{base}(iface={self.source_id}, id={self.ref_id})"
    if self.is_enum_ref:
      e = registry.resolve_enum(self.source_id, self.ref_id, ho_interface_id=ho_interface_id)
      return e.name if e else f"{base}(iface={self.source_id}, id={self.ref_id})"
    return f"{base}(iface={self.source_id}, id={self.ref_id})"


def _parse_type_seq(
  data: bytes | list[int],
  complex_ids: set[int],
) -> List[ParameterType]:
  """Shared variable-width parser for Hamilton type-ID byte sequences.

  Both GetMethod parameterTypes and GetStructs structureElementTypes encode types
  as a byte stream where simple types occupy 1 byte and complex references have
  variable width.

  For struct element types (complex_ids = _COMPLEX_STRUCT_TYPE_IDS), complex
  sentinels (30=STRUCTURE, 31=STRUCT_ARRAY, 32=ENUM, 35=ENUM_ARRAY) have two
  encoding formats determined by the second byte:

  - **Reference** (second byte ≤ 3): 3 bytes ``[sentinel, source_id, ref_id]``
    where source 1=global, 2=local, 3=network.
  - **Inline definition** (second byte = 4): variable width, terminated by
    ``0xEE`` (238). Typically 7 bytes: ``[sentinel, 4, base_type, 0, 1, 0, 0xEE]``.
    The ``base_type`` specifies the underlying wire type (1=I8, 2=I16, 3=I32).

  For method parameter types, only the 3-byte reference format is used.

  Args:
    data: Raw bytes or list of ints to parse.
    complex_ids: Set of type_id values that introduce a multi-byte entry.

  Returns:
    List of ParameterType, one per logical type entry.
  """
  _INLINE_MARKER = 4
  _INLINE_TERMINATOR = 0xEE  # 238

  ints = list(data) if isinstance(data, bytes) else data
  result: List[ParameterType] = []
  i = 0
  while i < len(ints):
    tid = ints[i]
    if tid in complex_ids and i + 2 < len(ints):
      second = ints[i + 1]
      if second == _INLINE_MARKER:
        # Inline type definition: scan forward to 0xEE terminator
        end = i + 2
        while end < len(ints) and ints[end] != _INLINE_TERMINATOR:
          end += 1
        end += 1  # consume the 0xEE byte itself
        # Store as ParameterType with the base wire type from byte [i+2]
        width = end - i
        base_type = ints[i + 2] if i + 2 < len(ints) else 0
        result.append(
          ParameterType(tid, source_id=_INLINE_MARKER, ref_id=base_type, _byte_width=width)
        )
        i = end
      else:
        # Standard 3-byte reference: [sentinel, source_id, ref_id]
        result.append(ParameterType(tid, source_id=second, ref_id=ints[i + 2], _byte_width=3))
        i += 3
    else:
      result.append(ParameterType(tid))
      i += 1
  return result


def _parse_type_ids(raw: str | bytes | None) -> List[ParameterType]:
  """Parse GetMethod parameterTypes blob. Thin wrapper around _parse_type_seq.

  Accepts bytes (preferred) or str — the device sends STRING (15) but the
  payload is binary, so callers must use parse_next_raw() to avoid UTF-8 errors.
  """
  if raw is None:
    return []
  data: list[int] = list(raw) if isinstance(raw, bytes) else [ord(c) for c in raw]
  return _parse_type_seq(data, _COMPLEX_METHOD_TYPE_IDS)


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

  @staticmethod
  def _signature_type_name(type_name: str) -> str:
    """Strip direction markers for human-readable signatures."""
    cleaned = type_name
    for marker in ("In", "Out", "RetVal"):
      cleaned = cleaned.replace(f" [{marker}]", "")
    return cleaned.strip()

  def signature_string(self) -> str:
    """Render the canonical method descriptor as a signature string."""
    if self.params:
      param_str = ", ".join(
        f"{p.name}: {self._signature_type_name(p.type_name)}" for p in self.params
      )
    else:
      param_str = "void"

    if self.return_shape == "void" or not self.returns:
      return_str = "void"
    elif self.return_shape == "scalar" and len(self.returns) == 1:
      ret = self.returns[0]
      ret_type = self._signature_type_name(ret.type_name)
      return_str = f"{ret.name}: {ret_type}" if ret.name != "ret0" else ret_type
    else:
      return_str = (
        "{ "
        + ", ".join(f"{r.name}: {self._signature_type_name(r.type_name)}" for r in self.returns)
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
  parameter_types: list[ParameterType] = field(default_factory=list)
  parameter_labels: list[str] = field(default_factory=list)
  return_types: list[ParameterType] = field(default_factory=list)
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
      return_categories = [get_introspection_type_category(rt.type_id) for rt in self.return_types]
      for i, type_name in enumerate(return_type_names):
        label = self.return_labels[i] if i < len(self.return_labels) else None
        returns.append(MethodFieldDescriptor(name=label or f"ret{i}", type_name=type_name))
      if len(returns) == 1 and not any(cat == "ReturnElement" for cat in return_categories):
        return_shape = "scalar"
      elif len(returns) > 0:
        # Includes ReturnElement records and explicit multi-return methods.
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
    their names (e.g. PickupTipParameters instead of struct(iface=1, id=57)).
    """
    return self.describe(registry).signature_string()

  def to_dict(self, registry: Optional["TypeRegistry"] = None) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    return self.describe(registry).to_dict()


_TLocal = TypeVar("_TLocal")


def _lookup_local_table_entry(
  tables: Dict[int, Dict[int, _TLocal]],
  ref_id: int,
) -> Optional[_TLocal]:
  """Resolve source_id=2 (local) refs when HOI interface id is unknown.

  ref_id is 1-based; struct/enum_id in tables is 0-based. Tables are keyed by HOI
  interface id from InterfaceDescriptors (e.g. 1 for API, 0 for introspection), not
  by the literal ``2`` in the wire triple. Prefers interface 1 when present, then
  scans other non-zero keys — unsafe if two interfaces reuse the same local index
  with different meanings. Prefer ``TypeRegistry.resolve_*(..., ho_interface_id=…)``.
  """
  idx = ref_id - 1
  if idx < 0:
    return None
  if 1 in tables:
    hit = tables[1].get(idx)
    if hit is not None:
      return hit
  for iid in sorted(k for k in tables if k != 0 and k != 1):
    hit = tables[iid].get(idx)
    if hit is not None:
      return hit
  return None


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

  source_id=0 (same-interface references) appears in nested struct field type bytes;
  indexing for method-level params and whether to use GlobalTypePool.resolve_struct_local
  vs. this registry should be validated on hardware — do not assume the same 1-based rule
  as source_id=2 locals.

  For source_id=2, pass ``ho_interface_id`` on ``resolve_struct`` / ``resolve_enum`` whenever
  the owning method or struct's interface is known (strict table lookup). Omitting it uses
  a legacy multi-interface fallback that may be ambiguous if two interfaces share a local index.

  Example:
    registry = await intro.build_type_registry(mph_addr)
    method = registry.get_method(interface_id=1, method_id=9)
    print(method.get_signature_string(registry))  # PickupTips(tipParameters: PickupTipParameters, ...)
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
      ``self.structs[ho_interface_id]``). Pass ``ho_interface_id`` for strict,
      interface-scoped resolution; if omitted, uses multi-interface fallback
      (see _lookup_local_table_entry).
    """
    if source_id == 1 and self.global_pool is not None:
      return self.global_pool.resolve_struct(ref_id)
    if source_id == 2:
      idx = ref_id - 1
      if idx < 0:
        return None
      if ho_interface_id is not None:
        return self.structs.get(ho_interface_id, {}).get(idx)
      return _lookup_local_table_entry(self.structs, ref_id)
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
    source_id=2: Local enums (same rules as resolve_struct). Pass ``ho_interface_id``
      for strict resolution; if omitted, multi-interface fallback applies.
    """
    if source_id == 1 and self.global_pool is not None:
      return self.global_pool.resolve_enum(ref_id)
    if source_id == 2:
      idx = ref_id - 1
      if idx < 0:
        return None
      if ho_interface_id is not None:
        return self.enums.get(ho_interface_id, {}).get(idx)
      return _lookup_local_table_entry(self.enums, ref_id)
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

  ``fields`` maps field names to ``ParameterType`` instances, preserving the
  full (type_id, source_id, ref_id) triple for fields that are complex
  references (type 30=STRUCTURE, 32=ENUM).  Call ``get_struct_string(registry)``
  to get human-readable names with struct/enum references resolved.
  """

  struct_id: int
  name: str
  fields: Dict[str, "ParameterType"]  # field_name -> ParameterType
  interface_id: Optional[int] = None  # Interface this struct was defined on

  @property
  def field_type_names(self) -> Dict[str, str]:
    """Get human-readable field type names using HamiltonDataType resolver."""
    return {name: _resolve_struct_field_type(pt) for name, pt in self.fields.items()}

  def to_dict(self, registry: Optional["TypeRegistry"] = None) -> dict:
    """Serialize to a plain dict suitable for YAML/JSON export."""
    ho_iid = self.interface_id
    fields = {
      name: _resolve_struct_field_type(pt, registry, ho_interface_id=ho_iid)
      for name, pt in self.fields.items()
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
      f"{name}: {_resolve_struct_field_type(pt, registry, ho_interface_id=ho_iid)}"
      for name, pt in self.fields.items()
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
    "year": ParameterType(type_id=5),  # U16
    "month": ParameterType(type_id=4),  # U8 (padded)
    "day": ParameterType(type_id=4),
    "hour": ParameterType(type_id=4),
    "minute": ParameterType(type_id=4),
    "second": ParameterType(type_id=4),
    "millisecond": ParameterType(type_id=5),  # U16
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


def _resolve_struct_field_type(
  pt: ParameterType,
  registry: Optional["TypeRegistry"] = None,
  *,
  ho_interface_id: Optional[int] = None,
) -> str:
  """Resolve a struct field's ParameterType to a human-readable type name.

  Struct field type_ids use the HamiltonDataType wire namespace (e.g. 40=F32,
  23=BOOL) -- not the method-parameter introspection namespace. Complex
  references (30=STRUCTURE, 32=ENUM) are resolved via the TypeRegistry when provided.

  Pass ``ho_interface_id`` as the owning struct's HOI interface id for local
  (source_id=2) field references.
  """
  if pt.is_complex and pt.source_id is not None and pt.ref_id is not None:
    if registry is not None:
      if pt.is_struct_ref:
        s = registry.resolve_struct(pt.source_id, pt.ref_id, ho_interface_id=ho_interface_id)
        if s:
          return f"struct({s.name})"
      elif pt.is_enum_ref:
        e = registry.resolve_enum(pt.source_id, pt.ref_id, ho_interface_id=ho_interface_id)
        if e:
          return e.name
    return f"ref(iface={pt.source_id}, id={pt.ref_id})"
  return resolve_type_id(pt.type_id)  # HamiltonDataType resolver


# ============================================================================
# INTROSPECTION COMMAND CLASSES
# ============================================================================


class GetObjectCommand(HamiltonCommand):
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


class GetMethodCommand(HamiltonCommand):
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
    return HoiParams().add(self.method_index, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_method response."""
    parser = HoiParamsParser(data)

    _, interface_id = parser.parse_next()
    _, call_type = parser.parse_next()
    _, method_id = parser.parse_next()
    _, name = parser.parse_next()

    # The remaining fragments are STRING types containing type IDs as bytes.
    # Complex types (struct/enum refs) are 3-byte triples [type_id, source_id, ref_id].
    # Labels are comma-separated, one per *logical* parameter (matching ParameterType count).
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

    parameter_types: list[ParameterType] = []
    parameter_labels: list[str] = []
    return_types: list[ParameterType] = []
    return_labels: list[str] = []

    for i, pt in enumerate(all_types):
      category = get_introspection_type_category(pt.type_id)
      label = all_labels[i] if i < len(all_labels) else None

      if category == "Argument":
        parameter_types.append(pt)
        if label:
          parameter_labels.append(label)
      elif category in ("ReturnElement", "ReturnValue"):
        return_types.append(pt)
        if label:
          return_labels.append(label)
      else:
        raise ValueError(
          f"Unknown introspection type_id={pt.type_id} ({resolve_introspection_type_name(pt.type_id)}); "
          "not in HoiObject mHoiParamTypes grid — update _HOI_PARAM_TYPE_GRID or add an override."
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


class GetSubobjectAddressCommand(HamiltonCommand):
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
    return HoiParams().add(self.subobject_index, U16)

  @dataclass(frozen=True)
  class Response:
    module_id: U16
    node_id: U16
    object_id: U16


class GetInterfacesCommand(HamiltonCommand):
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


class GetEnumsCommand(HamiltonCommand):
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
    return HoiParams().add(self.target_interface_id, U8)

  @dataclass(frozen=True)
  class Response:
    enum_names: StrArray
    value_counts: U32Array
    values: I32Array
    value_names: StrArray


class GetStructsCommand(HamiltonCommand):
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
    return HoiParams().add(self.target_interface_id, U8)

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
  """

  def __init__(self, backend):
    """Initialize introspection API.

    Args:
      backend: TCPBackend instance
    """
    self.backend = backend

  async def _resolve_target_address(self, addr_or_path: Union[Address, str]) -> Address:
    """Resolve Address or dot-path through the backend resolver consistently."""
    if isinstance(addr_or_path, str):
      return cast(Address, await self.backend.resolve_path(addr_or_path))
    return addr_or_path

  async def get_supported_interface0_method_ids(self, address: Address) -> Set[int]:
    """Return the set of Interface 0 method IDs this object supports.

    Calls GetObject to get method_count, then GetMethod(address, i) for each
    index and collects method_id for every method where interface_id == 0.
    Used to guard calls so we never send an Interface 0 command the object
    did not advertise.
    """
    cached = getattr(self.backend, "get_supported_interface0_method_ids", None)
    cache_store = getattr(self.backend, "_supported_interface0_method_ids", None)
    has_capability_cache = isinstance(cache_store, dict)
    if cached is not None and has_capability_cache:
      return cast(Set[int], await cached(address))

    obj = await self.get_object(address)
    supported: Set[int] = set()
    for i in range(obj.method_count):
      try:
        method = await self.get_method(address, i)
        if method.interface_id == 0:
          supported.add(method.method_id)
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.debug("get_method(%s, %d) failed: %s", address, i, e)
    return supported

  async def get_object(self, address: Address) -> ObjectInfo:
    """Get object metadata.

    Args:
      address: Object address to query

    Returns:
      Object metadata
    """
    command = GetObjectCommand(address)
    response = await self.backend.send_command(command, ensure_connection=False)
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
    response = await self.backend.send_command(command, ensure_connection=False)

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
    response = await self.backend.send_command(command, ensure_connection=False)
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
    response = await self.backend.send_command(command, ensure_connection=False)
    if response is None:
      raise RuntimeError("GetInterfacesCommand returned None")

    ids = list(response.interface_ids)
    names = list(response.interface_names)
    return [
      InterfaceInfo(
        interface_id=int(ids[i]),
        name=names[i] if i < len(names) else f"Interface_{ids[i]}",
        version="",
      )
      for i in range(len(ids))
    ]

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
    response = await self.backend.send_command(command, ensure_connection=False)
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

  async def get_structs_raw(self, address: Address, interface_id: int) -> tuple[bytes, List[dict]]:
    """Get raw GetStructs response bytes and a fragment-by-fragment breakdown.

    Use this to see exactly what the device sends so response parsing can
    match the wire format. Returns (params_bytes, inspect_hoi_params(params)).

    Example:
      raw, fragments = await intro.get_structs_raw(mph_addr, 1)
      for i, f in enumerate(fragments):
        print(f\"{i}: type_id={f['type_id']} len={f['length']} decoded={f['decoded']!r}\")
    """
    command = GetStructsCommand(address, interface_id)
    result = await self.backend.send_command(command, ensure_connection=False, return_raw=True)
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
    response = await self.backend.send_command(command, ensure_connection=False)
    if response is None:
      raise RuntimeError("GetStructsCommand returned None")

    struct_names = list(response.struct_names)
    # field_counts = numberStructureElements from the device: logical fields per struct.
    # Struct IDs are positional (0-indexed); the device does not send them.
    field_counts = [int(c) for c in response.field_counts]
    type_bytes = list(response.field_type_ids)  # flat byte array; some entries are 3-byte triples
    field_names = list(response.field_names)
    n_structs = len(field_counts)
    if n_structs == 0:
      return []

    # Walk type_bytes with a byte-level cursor (variable width: 1 byte for simple
    # types, 3 bytes for 0xE8 complex references). field_counts gives the number
    # of *logical* fields per struct, not the number of bytes to consume.
    byte_offset = 0  # cursor into type_bytes
    name_offset = 0  # cursor into field_names
    result: List[StructInfo] = []
    for i, cnt in enumerate(field_counts):
      name = struct_names[i] if i < len(struct_names) else f"Struct_{i}"
      parsed = _parse_type_seq(type_bytes[byte_offset:], _COMPLEX_STRUCT_TYPE_IDS)
      # Consume exactly `cnt` logical entries; advance byte_offset by the bytes used.
      type_entries = parsed[:cnt]
      bytes_used = sum(pt._byte_width for pt in type_entries)
      names_slice = field_names[name_offset : name_offset + cnt]
      fields = dict(zip(names_slice, type_entries))
      result.append(StructInfo(struct_id=i, name=name, fields=fields, interface_id=interface_id))
      byte_offset += bytes_used
      name_offset += cnt
    return result

  async def get_all_methods(
    self,
    address: Address,
    *,
    _supported: Optional[Set[int]] = None,
    _object_info: Optional[ObjectInfo] = None,
  ) -> List[MethodInfo]:
    """Get all methods for an object.

    Returns [] if the object does not support GetMethod (interface 0, method 2).

    Args:
      address: Object address
      _supported: Pre-computed supported Interface 0 method IDs (internal).
      _object_info: Pre-fetched ObjectInfo (internal; avoids redundant GetObject).

    Returns:
      List of all method signatures
    """
    if _object_info is None:
      _object_info = await self.get_object(address)
    if _supported is None:
      _supported = await self.get_supported_interface0_method_ids(address)
    if GET_METHOD not in _supported:
      logger.debug(
        "Object at %s does not support GetMethod (interface 0, method 2); returning []",
        address,
      )
      return []

    methods = []
    for i in range(_object_info.method_count):
      try:
        method = await self.get_method(address, i)
        methods.append(method)
      except _TRANSIENT_ERRORS:
        raise
      except Exception as e:
        logger.warning(f"Failed to get method {i} for {address}: {e}")

    return methods

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
      global_pool: Optional GlobalTypePool for resolving source_id=1 refs.
      _supported: Pre-computed supported Interface 0 method IDs (internal;
        avoids redundant device queries when the caller already has them).

    Returns:
      TypeRegistry with all type information for this object
    """
    address = await self._resolve_target_address(address)
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
      registry.methods = await self.get_all_methods(address, _supported=_supported)
    else:
      registry.methods = []

    for iface in interfaces:
      if GET_STRUCTS in _supported:
        structs = await self.get_structs(address, iface.interface_id)
        if structs:
          registry.structs[iface.interface_id] = {s.struct_id: s for s in structs}
      if GET_ENUMS in _supported:
        enums = await self.get_enums(address, iface.interface_id)
        if enums:
          registry.enums[iface.interface_id] = {e.enum_id: e for e in enums}

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
    that ParameterType.resolve_name() can find them.

    Args:
      address: Parent object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").
      subobject_addresses: Optional list of child addresses to include.
        If None, all direct subobjects are discovered automatically.
      global_pool: Optional GlobalTypePool for resolving source_id=1 refs.

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
    """Build the global type pool from global objects.

    This mirrors piglet's approach: walk each global object, iterate its
    interfaces, and collect all structs/enums in sequential encounter order.
    The resulting flat pool is used for source_id=1 lookups (1-based indexing).

    Args:
      global_addresses: List of global object addresses
        (from HamiltonTCPClient._global_object_addresses).

    Returns:
      GlobalTypePool with all global structs and enums.
    """
    pool = GlobalTypePool()

    for addr in global_addresses:
      try:
        supported = await self.get_supported_interface0_method_ids(addr)
        if GET_INTERFACES not in supported:
          continue

        interfaces = await self.get_interfaces(addr, _supported=supported)
        for iface in interfaces:
          if GET_STRUCTS in supported:
            structs = await self.get_structs(addr, iface.interface_id)
            pool.structs.extend(structs)
            pool.interface_structs[iface.interface_id] = {s.struct_id: s for s in structs}
          if GET_ENUMS in supported:
            enums = await self.get_enums(addr, iface.interface_id)
            pool.enums.extend(enums)
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
    methods = await self.get_all_methods(address)
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
    """One-liner: return a fully resolved method signature string.

    Looks up the method and resolves struct/enum references using the
    provided TypeRegistry (or falls back to unresolved names).

    Example::

      sig = await intro.resolve_signature("MLPrepRoot.MphRoot.MPH", 1, 9, mph_registry)
      print(sig)
      # PickupTips(tipParameters: PickupTipParameters, finalZ: f32, ...) -> ...

    Returns:
      Human-readable signature string, or a descriptive error string.
    """
    address = await self._resolve_target_address(address)
    method = await self.get_method_by_id(address, interface_id, method_id, registry=registry)
    if method is None:
      return f"<method not found: iface={interface_id} id={method_id} at {address}>"
    return method.get_signature_string(registry)


# ============================================================================
# STRUCT / COMMAND VALIDATION
# ============================================================================


def _normalize_name(name: str) -> str:
  """Normalize a name for comparison (remove underscores, make lowercase).

  Allows Pythonic `snake_case` (e.g. `z_liquid_exit_speed`) to match
  Hamilton's arbitrary PascalCase (`ZLiquidExitSpeed` or `Zliquidexitspeed`).
  """
  return name.replace("_", "").lower()


def _get_wire_type_id(annotation) -> Optional[int]:
  """Extract HamiltonDataType type_id from an Annotated type alias.

  Works for all our wire types: F32, PaddedBool, U32, WEnum, Str,
  Annotated[X, Struct()], Annotated[list[X], StructArray()], etc.

  Returns None if the annotation doesn't carry a WireType.
  """
  # Handle typing.Annotated
  metadata = getattr(annotation, "__metadata__", None)
  if metadata:
    for m in metadata:
      if hasattr(m, "type_id"):
        return cast(int, m.type_id)
  return None


def _get_nested_dataclass(annotation):
  """For Annotated[SomeDataclass, Struct()], return SomeDataclass. Else None."""
  args = getattr(annotation, "__args__", None)
  if not args:
    return None
  base_type = args[0]
  # For Annotated[list[X], StructArray()], dig into the list's inner type
  inner_args = getattr(base_type, "__args__", None)
  if inner_args:
    base_type = inner_args[0]
  import dataclasses

  if dataclasses.is_dataclass(base_type):
    return base_type
  return None


@dataclass
class FieldMismatch:
  """One field-level mismatch between hand-crafted and introspected definitions."""

  field_name: str
  issue: str  # e.g. "missing", "extra", "type mismatch", "order mismatch"
  expected: str = ""
  actual: str = ""

  def __str__(self):
    s = f"  {self.field_name}: {self.issue}"
    if self.expected or self.actual:
      s += f" (expected={self.expected}, actual={self.actual})"
    return s


@dataclass
class ValidationResult:
  """Result of comparing a hand-crafted dataclass against introspection."""

  name: str
  passed: bool = False
  mismatches: List[FieldMismatch] = field(default_factory=list)
  children: List["ValidationResult"] = field(default_factory=list)

  def __str__(self):
    icon = "✅" if self.passed else "❌"
    lines = [f"{icon} {self.name}"]
    for m in self.mismatches:
      lines.append(str(m))
    for child in self.children:
      for line in str(child).split("\n"):
        lines.append(f"    {line}")
    return "\n".join(lines)


def validate_struct(
  dataclass_cls,
  introspected: StructInfo,
  pool: Optional[GlobalTypePool] = None,
  registry: Optional["TypeRegistry"] = None,
) -> ValidationResult:
  """Compare a hand-crafted dataclass against an introspected StructInfo.

  Checks field count, field names (snake_case → PascalCase), field types
  (extracts type_id from Annotated metadata), and field order. For nested
  structs (Annotated[X, Struct()]), recursively validates the child struct
  when a GlobalTypePool and/or TypeRegistry can resolve the nested ref
  (global, same-interface, or local source_id=2 with registry + interface id).

  Args:
    dataclass_cls: The hand-crafted dataclass class (not an instance).
    introspected: The introspected StructInfo from the device.
    pool: Optional GlobalTypePool for nested global (1) and same-interface (0) refs.
    registry: Optional TypeRegistry for nested local (source_id=2) refs.

  Returns:
    ValidationResult with pass/fail and detailed mismatches.
  """
  import dataclasses as dc
  import typing

  result = ValidationResult(name=dataclass_cls.__name__)
  mismatches = result.mismatches

  # Get hand-crafted fields
  hints = typing.get_type_hints(dataclass_cls, include_extras=True)
  hand_fields = list(dc.fields(dataclass_cls))
  hand_names = [f.name for f in hand_fields]
  hand_norm = [_normalize_name(n) for n in hand_names]

  # Get introspected fields
  intro_names = list(introspected.fields.keys())
  intro_norm = [_normalize_name(n) for n in intro_names]
  intro_types = list(introspected.fields.values())

  # 1. Field count
  if len(hand_names) != len(intro_names):
    mismatches.append(
      FieldMismatch(
        field_name="(count)",
        issue="field count mismatch",
        expected=str(len(intro_names)),
        actual=str(len(hand_names)),
      )
    )

  # 2. Field names (order-aware)
  for i, (hn_norm, in_norm) in enumerate(zip(hand_norm, intro_norm)):
    if hn_norm != in_norm:
      mismatches.append(
        FieldMismatch(
          field_name=hand_names[i],
          issue=f"name mismatch at position {i}",
          expected=intro_names[i],
          actual=hand_names[i],
        )
      )

  # 3. Extra / missing fields
  hand_set = set(hand_norm)
  intro_set = set(intro_norm)

  # For error reporting, we want the original casing, so we build reverse maps
  hand_map = {hn_norm: h for hn_norm, h in zip(hand_norm, hand_names)}
  intro_map = {in_norm: i for in_norm, i in zip(intro_norm, intro_names)}

  for missing_norm in intro_set - hand_set:
    original_intro = intro_map[missing_norm]
    mismatches.append(FieldMismatch(field_name=original_intro, issue="missing in hand-crafted"))
  for extra_norm in hand_set - intro_set:
    original_hand = hand_map[extra_norm]
    mismatches.append(
      FieldMismatch(field_name=original_hand, issue="extra in hand-crafted (not in introspection)")
    )

  # 4. Field types (where names match)
  for i, (hand_name, intro_name) in enumerate(zip(hand_names, intro_names)):
    if _normalize_name(hand_name) != _normalize_name(intro_name):
      continue  # Already reported as name mismatch
    annotation = hints.get(hand_name)
    if annotation is None:
      continue
    hand_type_id = _get_wire_type_id(annotation)
    intro_pt = intro_types[i]
    if hand_type_id is not None and hand_type_id != intro_pt.type_id:
      try:
        from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import HamiltonDataType

        expected_name = HamiltonDataType(intro_pt.type_id).name
        actual_name = HamiltonDataType(hand_type_id).name
      except ValueError:
        expected_name = str(intro_pt.type_id)
        actual_name = str(hand_type_id)
      mismatches.append(
        FieldMismatch(
          field_name=hand_name,
          issue="type mismatch",
          expected=expected_name,
          actual=actual_name,
        )
      )

    # 5. Recursive validation for nested structs
    if (
      intro_pt.is_complex
      and intro_pt.source_id is not None
      and intro_pt.ref_id is not None
      and intro_pt.type_id == 30
    ):  # STRUCTURE
      nested_cls = _get_nested_dataclass(annotation)
      if nested_cls:
        nested_struct: Optional[StructInfo] = None
        if intro_pt.source_id == 1 and pool is not None:
          nested_struct = pool.resolve_struct(intro_pt.ref_id)
        elif intro_pt.source_id == 0 and pool is not None and introspected.interface_id is not None:
          nested_struct = pool.resolve_struct_local(introspected.interface_id, intro_pt.ref_id)
        elif (
          intro_pt.source_id == 2 and registry is not None and introspected.interface_id is not None
        ):
          nested_struct = registry.resolve_struct(
            2, intro_pt.ref_id, ho_interface_id=introspected.interface_id
          )
        if nested_struct:
          child_result = validate_struct(nested_cls, nested_struct, pool, registry=registry)
          result.children.append(child_result)

  result.passed = len(mismatches) == 0 and all(c.passed for c in result.children)
  return result


def validate_command(
  command_cls,
  registry: TypeRegistry,
  pool: GlobalTypePool,
  interface_id: int = 1,
) -> ValidationResult:
  """Compare a PrepCommand against its introspected method signature.

  Matches the command's command_id to the introspected method_id on the given
  interface. Validates that the command's struct parameters match the method's
  expected struct types.

  Args:
    command_cls: The PrepCommand subclass.
    registry: TypeRegistry with the object's methods.
    pool: GlobalTypePool for resolving struct refs.
    interface_id: Interface ID to look up the method on (default 1 = Pipettor).

  Returns:
    ValidationResult with pass/fail and details.
  """
  import dataclasses as dc
  import typing

  cmd_id = getattr(command_cls, "command_id", None)
  result = ValidationResult(name=f"{command_cls.__name__} (cmd={cmd_id})")

  if cmd_id is None:
    result.mismatches.append(FieldMismatch(field_name="(class)", issue="no command_id attribute"))
    result.passed = False
    return result

  # Find matching introspected method
  method = registry.get_method(interface_id, cmd_id)
  if method is None:
    result.mismatches.append(
      FieldMismatch(
        field_name="(method)", issue=f"no introspected method for [{interface_id}:{cmd_id}]"
      )
    )
    result.passed = False
    return result

  result.name = f"{command_cls.__name__} ↔ {method.name} [{interface_id}:{cmd_id}]"

  # Get command's payload fields (exclude 'dest' and class-level attrs)
  hints = typing.get_type_hints(command_cls, include_extras=True)
  payload_fields = [f for f in dc.fields(command_cls) if f.name != "dest"]

  # Match struct payload fields to introspected parameter types positionally
  struct_fields = [
    (pf, hints.get(pf.name))
    for pf in payload_fields
    if _get_nested_dataclass(hints.get(pf.name)) is not None
  ]
  struct_params = [
    pt
    for pt in method.parameter_types
    if pt.is_complex and pt.source_id is not None and pt.ref_id is not None
  ]

  for (pf, annotation), pt in zip(struct_fields, struct_params):
    ref_id = pt.ref_id
    assert ref_id is not None, "struct_params filtered for ref_id is not None"
    if pt.source_id == 0:
      # Same-interface ref: needs correct HOI interface id for GlobalTypePool.resolve_struct_local
      # vs. registry — validate on hardware before wiring method-level validation here.
      intro_struct = None
    elif pt.source_id == 2:
      intro_struct = registry.resolve_struct(
        pt.source_id, ref_id, ho_interface_id=method.interface_id
      )
    else:
      src_id = pt.source_id
      assert src_id is not None, "struct_params filtered for source_id is not None"
      intro_struct = registry.resolve_struct(src_id, ref_id)
    nested_cls = _get_nested_dataclass(annotation)
    if intro_struct and nested_cls:
      child_result = validate_struct(nested_cls, intro_struct, pool, registry=registry)
      child_result.name = f"{pf.name} → {intro_struct.name} (ref={pt.ref_id})"
      result.children.append(child_result)

  result.passed = all(c.passed for c in result.children) and len(result.mismatches) == 0
  return result
