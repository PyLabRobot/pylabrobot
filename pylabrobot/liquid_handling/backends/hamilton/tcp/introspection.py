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

    # Diagnose a COMMAND_EXCEPTION:
    print(await intro.diagnose_error(str(e), registry))

    # Resolve a method signature:
    sig = await intro.resolve_signature("MLPrepRoot.MphRoot.MPH", 1, 9, registry)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TypeVar, Union, cast

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
# INTROSPECTION TYPE MAPPING
# ============================================================================
# Introspection type IDs are separate from HamiltonDataType wire encoding types.
# These are used for method signature display/metadata, not binary encoding.

# Type ID ranges for categorization:
# - Argument types: Method parameters (input)
# - ReturnElement types: Multiple return values (struct fields)
# - ReturnValue types: Single return value

_INTROSPECTION_TYPE_NAMES: dict[int, str] = {
  # Void (0) - used for empty/placeholder parameters
  0: "void",
  # Argument types (1-8, 33, 41, 45, 49, 53, 61, 66, 82, 102)
  1: "i8",
  2: "u8",
  3: "i16",
  4: "u16",
  5: "i32",
  6: "u32",
  7: "str",
  8: "bytes",
  33: "bool",
  41: "List[i16]",
  45: "List[u16]",
  49: "List[i32]",
  53: "List[u32]",
  57: "struct",  # Complex type: (57, source_id, ref_id) → single struct
  61: "List[struct]",  # Complex type: (61, source_id, ref_id) → list of structs
  63: "struct",  # Return element: struct ref in a return list (e.g. MoveYAbsolute return)
  66: "List[bool]",
  77: "List[str]",
  82: "List[enum]",  # Complex type, needs source_id + enum_id
  102: "f32",
  113: "List[f32]",  # Inferred from CalibrateZTipHeight(tipHeights) context; not protocol-confirmed
  # ReturnElement types (18-24, 35, 43, 47, 51, 55, 68, 76)
  18: "u8",
  19: "i16",
  20: "u16",
  21: "i32",
  22: "u32",
  23: "str",
  24: "bytes",
  35: "bool",
  43: "List[i16]",
  47: "List[u16]",
  51: "List[i32]",
  55: "List[u32]",
  68: "List[bool]",
  76: "List[str]",
  # ReturnValue types (25-32, 36, 44, 48, 52, 56, 69, 81, 85, 104, 105)
  25: "i8",
  26: "u8",
  27: "i16",
  28: "u16",
  29: "i32",
  30: "u32",
  31: "str",
  32: "bytes",
  36: "bool",
  44: "List[i16]",
  48: "List[u16]",
  52: "List[i32]",
  56: "List[u32]",
  69: "List[bool]",
  81: "enum",  # Complex type, needs source_id + enum_id
  85: "enum",  # Complex type, needs source_id + enum_id
  104: "f32",
  105: "f32",
  # Complex types (60, 64, 78) - these need source_id + id
  60: "struct",  # ReturnValue, needs source_id + struct_id
  64: "struct",  # ReturnValue, needs source_id + struct_id
  78: "enum",  # Argument, needs source_id + enum_id
}

# Type ID sets for categorization
# 78 = enum (Argument); 60, 64 = struct (ReturnValue) — see _INTROSPECTION_TYPE_NAMES comments
_ARGUMENT_TYPE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 33, 41, 45, 49, 53, 57, 61, 66, 77, 78, 82, 102, 113}
_RETURN_ELEMENT_TYPE_IDS = {18, 19, 20, 21, 22, 23, 24, 35, 43, 47, 51, 55, 63, 68, 76}
_RETURN_VALUE_TYPE_IDS = {
  25,
  26,
  27,
  28,
  29,
  30,
  31,
  32,
  36,
  44,
  48,
  52,
  56,
  60,
  64,
  69,
  81,
  85,
  104,
  105,
}

# Complex type sentinels: byte values that begin a 3-byte triple [type_id, source_id, ref_id].
# The two contexts (method parameterTypes vs struct structureElementTypes) use different sentinels.
_COMPLEX_METHOD_TYPE_IDS = {57, 60, 61, 63, 64, 78, 81, 82, 85}  # GetMethod parameterTypes triples
_COMPLEX_STRUCT_TYPE_IDS = {30, 31, 32, 35}  # STRUCTURE=30, STRUCT_ARRAY=31, ENUM=32, ENUM_ARRAY=35
# Backward-compat alias (used by ParameterType.is_complex for method parameters)
_COMPLEX_TYPE_IDS = _COMPLEX_METHOD_TYPE_IDS

# HC_RESULT codes returned in COMMAND_EXCEPTION / STATUS_EXCEPTION (extend as observed)
_HC_RESULT_DESCRIPTIONS: Dict[int, str] = {
  0x0000: "success",
  0x0005: "invalid parameter / not supported",
  0x0006: "unknown command",
  0x0E01: "door unlocked / safety interlock (command not allowed while door open)",
  0x0200: "hardware error",
  0x020A: "hardware not ready / axis error",
  # Empirically identified — require further validation:
  0x0011: "parameter value out of valid range [empirical, needs validation]",
  0x0F03: "drive initialization failed (reference/homing run failure) [empirical, needs validation]",
  0x0F04: "X position out of allowed movement range [empirical, needs validation]",
  0x0F05: "Y position out of allowed movement range [empirical, needs validation]",
  0x0F06: "Z position out of allowed movement range [empirical, needs validation]",
  0x0F08: "tip pickup failed (tip not detected at expected position) [empirical, needs validation]",
}


def describe_hc_result(code: int) -> str:
  """Return human-readable description for an HC_RESULT code from device errors."""
  return _HC_RESULT_DESCRIPTIONS.get(code, f"HC_RESULT=0x{code:04X} (unknown)")


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

  def get_signature_string(self, registry: Optional["TypeRegistry"] = None) -> str:
    """Get method signature as a readable string.

    If a TypeRegistry is provided, struct/enum references are resolved to
    their names (e.g. PickupTipParameters instead of struct(iface=1, id=57)).
    """
    iid = self.interface_id
    if self.parameter_types:
      param_type_names = [pt.resolve_name(registry, ho_interface_id=iid) for pt in self.parameter_types]
      if self.parameter_labels and len(self.parameter_labels) == len(param_type_names):
        params = [
          f"{label}: {type_name}"
          for label, type_name in zip(self.parameter_labels, param_type_names)
        ]
        param_str = ", ".join(params)
      else:
        param_str = ", ".join(param_type_names)
    else:
      param_str = "void"

    if self.return_types:
      return_type_names = [rt.resolve_name(registry, ho_interface_id=iid) for rt in self.return_types]
      return_categories = [get_introspection_type_category(rt.type_id) for rt in self.return_types]
      if any(cat == "ReturnElement" for cat in return_categories):
        if self.return_labels and len(self.return_labels) == len(return_type_names):
          returns = [
            f"{label}: {type_name}"
            for label, type_name in zip(self.return_labels, return_type_names)
          ]
          return_str = f"{{ {', '.join(returns)} }}"
        else:
          return_str = f"{{ {', '.join(return_type_names)} }}"
      elif len(return_type_names) == 1:
        if self.return_labels and len(self.return_labels) == 1:
          return_str = f"{self.return_labels[0]}: {return_type_names[0]}"
        else:
          return_str = return_type_names[0]
      else:
        return_str = "void"
    else:
      return_str = "void"

    return f"[{self.interface_id}:{self.method_id}] {self.name}({param_str}) -> {return_str}"


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


@dataclass
class EnumInfo:
  """Enum definition from introspection."""

  enum_id: int
  name: str
  values: Dict[str, int]


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
        logger.warning(
          "Unknown introspection type category for type_id=%d; treating as parameter", pt.type_id
        )
        parameter_types.append(pt)
        if label:
          parameter_labels.append(label)

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

  def _resolve_address(self, addr_or_path: Union[Address, str]) -> Address:
    """Resolve dot-path string to Address using the backend's registry, or return Address as-is."""
    if isinstance(addr_or_path, str):
      return cast(Address, self.backend._registry.address(addr_or_path))
    return addr_or_path

  async def get_supported_interface0_method_ids(self, address: Address) -> Set[int]:
    """Return the set of Interface 0 method IDs this object supports.

    Calls GetObject to get method_count, then GetMethod(address, i) for each
    index and collects method_id for every method where interface_id == 0.
    Used to guard calls so we never send an Interface 0 command the object
    did not advertise.
    """
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
    address = self._resolve_address(address)
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
    address = self._resolve_address(address)
    supported = await self.get_supported_interface0_method_ids(address)
    registry = await self.build_type_registry(address, global_pool=global_pool, _supported=supported)

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
    address = self._resolve_address(address)
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
    address = self._resolve_address(address)
    method = await self.get_method_by_id(address, interface_id, method_id)
    if method is None:
      return f"<method not found: iface={interface_id} id={method_id} at {address}>"
    return method.get_signature_string(registry)

  async def resolve_error(
    self,
    address: Union[Address, str],
    interface_id: int,
    method_id: int,
    registry: Optional[TypeRegistry] = None,
    error_text: str = "",
    hc_result: Optional[int] = None,
  ) -> str:
    """Build an informative error diagnostic from a COMMAND_EXCEPTION.

    Resolves the object path, method signature, and expected parameters
    so the user can see exactly what the firmware expected. When
    hc_result is provided, appends a human-readable device error line
    via describe_hc_result().

    Example::

      info = await intro.resolve_error(addr, 1, 9, mph_registry, hc_result=0x0005)
      print(info)
      # Error on MLPrepRoot.MphRoot.MPH (57345:1:4352)
      #   Method [1:9] PickupTips(tipParameters: PickupTipParameters, ...)
      #   Expected params: tipParameters, finalZ, tipDefinition, ...
      #   Device error: invalid parameter / not supported (HC_RESULT=0x0005)

    Args:
      address: Object address or dot-path from the error.
      interface_id: Interface ID from the error.
      method_id: Method/command ID from the error.
      registry: Optional TypeRegistry for resolving struct/enum names.
      error_text: Raw error string (used when hc_result not provided).
      hc_result: HC_RESULT code from the device (e.g. 0x0005); if set, shown via describe_hc_result().

    Returns:
      Multi-line diagnostic string.
    """
    address = self._resolve_address(address)
    lines: list[str] = []

    path = self.backend._registry.path(address) if hasattr(self.backend, "_registry") else None
    if path:
      lines.append(f"Error on {path} ({address})")
    else:
      lines.append(f"Error on {address}")

    method = await self.get_method_by_id(address, interface_id, method_id)
    if method:
      sig = method.get_signature_string(registry)
      lines.append(f"  Method [{interface_id}:{method_id}] {sig}")
      if method.parameter_labels:
        lines.append(f"  Expected params: {', '.join(method.parameter_labels)}")
      for pt in method.parameter_types:
        if pt.is_complex:
          resolved = pt.resolve_name(registry, ho_interface_id=method.interface_id)
          lines.append(f"  Param type {pt.type_id}: {resolved}")
    else:
      lines.append(f"  Method [{interface_id}:{method_id}] <not found>")

    if hc_result is not None:
      lines.append(f"  Device error: {describe_hc_result(hc_result)}")
    if error_text:
      lines.append(f"  Device said: {error_text}")

    return "\n".join(lines)

  @staticmethod
  def parse_error_u8_array_message(error_string: str) -> Optional[str]:
    """Extract the decoded U8_ARRAY device message from a Hamilton error string.

    The parsed error string may contain ``U8_ARRAY=...`` (or ``U8_ARRAY="..."``)
    with the instrument's human-readable message. Returns that value or None.
    """
    import re

    # Match U8_ARRAY= then either quoted content or rest until "; " or " [" or end
    m = re.search(r"U8_ARRAY=(?:\"([^\"]*)\"|([^;[\]]*?)(?:\s*;\s*|\s*\[|$))", error_string)
    if not m:
      return None
    return (m.group(1) or m.group(2) or "").strip() or None

  @staticmethod
  def parse_error_address(
    error_string: str,
  ) -> Optional[tuple[Address, int, int, int]]:
    """Extract (address, interface_id, method_id, hc_result) from a COMMAND_EXCEPTION string.

    The Hamilton error string format includes the address as
    ``0xMMMM.0xNNNN.0xOOOO:0xII,0xCCCC,0xRRRR`` where the first part is
    the 3-part object address and II/CCCC/RRRR encode interface, command, and HC_RESULT.

    Example::

      result = HamiltonIntrospection.parse_error_address(
        "0x0001.0x0001.0x1100:0x01,0x0009,0x020A"
      )
      if result:
        addr, iface_id, method_id, hc_result = result

    Returns:
      (Address, interface_id, method_id, hc_result) or None if parsing fails.
    """
    import re

    m = re.search(
      r"0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)\.0x([0-9a-fA-F]+)"
      r":0x([0-9a-fA-F]+),0x([0-9a-fA-F]+)(?:,0x([0-9a-fA-F]+))?",
      error_string,
    )
    if not m:
      return None
    module_id = int(m.group(1), 16)
    node_id = int(m.group(2), 16)
    object_id = int(m.group(3), 16)
    interface_id = int(m.group(4), 16)
    method_id = int(m.group(5), 16)
    hc_result = int(m.group(6), 16) if m.group(6) else 0
    return Address(module_id, node_id, object_id), interface_id, method_id, hc_result

  async def diagnose_error(
    self,
    error_string: str,
    registry: Optional[TypeRegistry] = None,
  ) -> str:
    """One-liner: parse a COMMAND_EXCEPTION string and return a full diagnostic.

    Combines parse_error_address() + resolve_error() into a single call.
    Pass the raw error text (e.g. from a RuntimeError message) and get
    back a human-readable diagnostic.

    Example::

      try:
        await backend.send_command(cmd)
      except RuntimeError as e:
        print(await intro.diagnose_error(str(e), mph_registry))

    Returns:
      Multi-line diagnostic string, or the original error if parsing fails.
    """
    parsed = self.parse_error_address(error_string)
    if parsed is None:
      return f"Could not parse error address from: {error_string}"
    address, interface_id, method_id, hc_result = parsed
    error_text = self.parse_error_u8_array_message(error_string) or ""
    return await self.resolve_error(
      address,
      interface_id,
      method_id,
      registry=registry,
      hc_result=hc_result,
      error_text=error_text,
    )


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
          intro_pt.source_id == 2
          and registry is not None
          and introspected.interface_id is not None
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
      intro_struct = registry.resolve_struct(pt.source_id, ref_id)
    nested_cls = _get_nested_dataclass(annotation)
    if intro_struct and nested_cls:
      child_result = validate_struct(nested_cls, intro_struct, pool, registry=registry)
      child_result.name = f"{pf.name} → {intro_struct.name} (ref={pt.ref_id})"
      result.children.append(child_result)

  result.passed = all(c.passed for c in result.children) and len(result.mismatches) == 0
  return result
