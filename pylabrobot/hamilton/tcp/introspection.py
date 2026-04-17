"""Hamilton TCP Introspection API.

Wraps a session backend (:class:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient`)
to provide dynamic discovery via Interface 0 methods (GetObject, GetMethod,
GetStructs, GetEnums, GetInterfaces, GetSubobjectAddress).

**Canonical usage:** use :attr:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient.introspection`
(do not construct :class:`HamiltonIntrospection` from application code).

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
from typing import Any, Dict, List, Literal, Optional, Protocol, Sequence, Set, Tuple, Union, cast

from pylabrobot.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.hamilton.tcp.messages import (
  PADDED_FLAG,
  HoiParams,
  HoiParamsParser,
  inspect_hoi_params,
)
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.tcp.wire_types import (
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


class HamiltonTCPIntrospectionBackend(Protocol):
  """Structural type for objects passed to :class:`HamiltonIntrospection`.

  **Production:** :class:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient` implements this
  Protocol (transport, registry, session caches, ``send_command``).

  **Tests:** provide a minimal object with the same methods/properties so introspection can be
  exercised without a socket—see ``tcp_tests`` (e.g. fake ``Backend`` with registry roots and
  patched ``HamiltonIntrospection`` methods). This is a typing contract only; there is no separate
  runtime "backend" class besides the client.
  """

  @property
  def registry(self) -> Any: ...

  def get_root_object_addresses(self) -> list[Address]: ...

  @property
  def global_object_addresses(self) -> Sequence[Address]: ...

  async def send_command(
    self,
    command: HamiltonCommand,
    *,
    ensure_connection: bool = True,
    return_raw: bool = False,
    raise_on_error: bool = True,
    read_timeout: Optional[float] = None,
  ) -> Any: ...

  async def resolve_path(self, path: str) -> Address: ...


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
# Introspection type IDs are separate from HamiltonDataType wire encoding types.
# Rows = firmware scalar or array kinds; columns = In, Out, InOut, RetVal
# (HoiParameterType.Direction). Source: vendor protocol reference mHoiParamTypes[31,4].


@dataclass(frozen=True)
class _HoiTypeRow:
  """One row in vendor mHoiParamTypes[31,4] with readable display metadata."""

  dotnet_name: str
  display_name: str
  ids: tuple[int, int, int, int]  # [In, Out, InOut, RetVal]


_HOI_TYPE_ROWS: tuple[_HoiTypeRow, ...] = (
  _HoiTypeRow("i8", "i8", (1, 17, 9, 25)),
  _HoiTypeRow("i16", "i16", (3, 19, 11, 27)),
  _HoiTypeRow("i32", "i32", (5, 21, 13, 29)),
  _HoiTypeRow("u8", "u8", (2, 18, 10, 26)),
  _HoiTypeRow("u16", "u16", (4, 20, 12, 28)),
  _HoiTypeRow("u32", "u32", (6, 22, 14, 30)),
  _HoiTypeRow("str", "str", (7, 23, 15, 31)),
  _HoiTypeRow("bool", "bool", (33, 35, 34, 36)),
  _HoiTypeRow("i8[]", "List[i8]", (37, 39, 38, 40)),
  _HoiTypeRow("i16[]", "List[i16]", (41, 43, 42, 44)),
  _HoiTypeRow("i32[]", "List[i32]", (49, 51, 50, 52)),
  _HoiTypeRow("u8[]", "bytes", (8, 24, 16, 32)),
  _HoiTypeRow("u16[]", "List[u16]", (45, 47, 46, 48)),
  _HoiTypeRow("u32[]", "List[u32]", (53, 55, 54, 56)),
  _HoiTypeRow("bool[]", "List[bool]", (66, 68, 67, 69)),
  _HoiTypeRow("HcResult", "HcResult", (70, 72, 71, 73)),
  _HoiTypeRow("struct", "struct", (57, 59, 58, 60)),
  _HoiTypeRow("struct[]", "List[struct]", (61, 63, 62, 64)),
  _HoiTypeRow("str[]", "List[str]", (74, 76, 75, 77)),
  _HoiTypeRow("enum", "enum", (78, 80, 79, 81)),
  _HoiTypeRow("enum[]", "List[enum]", (82, 84, 83, 85)),
  _HoiTypeRow("i64", "i64", (86, 88, 87, 89)),
  _HoiTypeRow("u64", "u64", (90, 92, 91, 93)),
  _HoiTypeRow("f32", "f32", (94, 96, 95, 97)),
  _HoiTypeRow("f64", "f64", (98, 100, 99, 101)),
  _HoiTypeRow("i64[]", "List[i64]", (102, 104, 103, 105)),
  _HoiTypeRow("u64[]", "List[u64]", (106, 108, 107, 109)),
  _HoiTypeRow("f32[]", "List[f32]", (110, 112, 111, 113)),
  _HoiTypeRow("f64[]", "List[f64]", (114, 116, 115, 117)),
  _HoiTypeRow("HoiResult", "HoiResult", (118, 120, 119, 121)),
  _HoiTypeRow("padding", "padding", (0, 0, 0, 0)),
)

_COMPLEX_METHOD_ROW_NAMES = frozenset(
  {
    "HcResult",
    "struct",
    "struct[]",
    "str[]",
    "enum",
    "enum[]",
    "HoiResult",
  }
)

_HOI_PARAM_DIRECTION: tuple[str, ...] = ("In", "Out", "InOut", "RetVal")


def _build_introspection_maps() -> tuple[dict[int, str], set[int], set[int], set[int], set[int]]:
  names: dict[int, str] = {0: "void"}
  arg_ids: set[int] = set()
  ret_el_ids: set[int] = set()
  ret_val_ids: set[int] = set()
  complex_method_ids: set[int] = set()
  for row in _HOI_TYPE_ROWS:
    for ci, tid in enumerate(row.ids):
      if tid == 0:
        continue
      d = _HOI_PARAM_DIRECTION[ci]
      disp = row.display_name
      names[tid] = f"{disp} [{d}]"
      if ci in (0, 2):
        arg_ids.add(tid)
      elif ci == 1:
        ret_el_ids.add(tid)
      elif ci == 3:
        ret_val_ids.add(tid)
      if row.dotnet_name in _COMPLEX_METHOD_ROW_NAMES:
        complex_method_ids.add(tid)

  return names, arg_ids, ret_el_ids, ret_val_ids, complex_method_ids


(
  _INTROSPECTION_TYPE_NAMES,
  _ARGUMENT_TYPE_IDS,
  _RETURN_ELEMENT_TYPE_IDS,
  _RETURN_VALUE_TYPE_IDS,
  _COMPLEX_METHOD_TYPE_IDS,
) = _build_introspection_maps()

# Empirical device behavior: type_id=113 appears as Argument on some firmware,
# despite the static grid column implying RetVal.
_INTROSPECTION_TYPE_NAMES[113] = "List[f32] [In] (empirical)"
_ARGUMENT_TYPE_IDS.add(113)

_COMPLEX_STRUCT_TYPE_IDS = {30, 31, 32, 35}  # STRUCTURE=30, STRUCT_ARRAY=31, ENUM=32, ENUM_ARRAY=35
_STRUCT_REF_TYPE_IDS = frozenset({30, 31, 57, 60, 61, 63, 64})
_ENUM_REF_TYPE_IDS = frozenset({32, 35, 78, 81, 82, 85})
_ALL_COMPLEX_TYPE_IDS = frozenset(_COMPLEX_METHOD_TYPE_IDS | _COMPLEX_STRUCT_TYPE_IDS)


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


class ObjectRegistry:
  """Object graph cache keyed by both path and address."""

  def __init__(self):
    self._objects: Dict[str, ObjectInfo] = {}
    self._address_to_path: Dict[Address, str] = {}
    self._root_addresses: List[Address] = []

  def set_root_addresses(self, addresses: List[Address]) -> None:
    self._root_addresses = list(addresses)

  def get_root_addresses(self) -> List[Address]:
    return list(self._root_addresses)

  def register(self, path: str, obj: ObjectInfo) -> None:
    self._objects[path] = obj
    self._address_to_path[obj.address] = path

  def path(self, address: Address) -> Optional[str]:
    return self._address_to_path.get(address)

  async def resolve(self, path: str, transport: Any) -> Address:
    """Resolve dot-path to address via lazy introspection."""
    if path in self._objects:
      return cast(Address, self._objects[path].address)

    parts = [p for p in path.split(".") if p]
    if not parts:
      raise KeyError(f"Invalid path: '{path}'")

    parent_path = ".".join(parts[:-1])
    child_name = parts[-1]

    introspection_obj = getattr(transport, "introspection", None)
    if introspection_obj is None:
      raise TypeError("ObjectRegistry.resolve requires transport.introspection")
    introspection = cast("HamiltonIntrospection", introspection_obj)
    if not parent_path:
      if not self._root_addresses:
        raise KeyError("No root addresses; run discovery first")
      parent_addr = self._root_addresses[0]
      parent_info = await introspection.get_object(parent_addr)
      parent_info.children = {}
      self.register(parent_info.name, parent_info)
      if parent_info.name == child_name:
        return parent_info.address
      raise KeyError(f"Root object is '{parent_info.name}', not '{child_name}'")

    parent_addr = await self.resolve(parent_path, transport)
    parent_info = self._objects[parent_path]
    supported = await introspection.get_supported_interface0_method_ids(parent_addr)
    if GET_SUBOBJECT_ADDRESS not in supported:
      raise KeyError(
        f"Object at path '{parent_path}' does not support GetSubobjectAddress "
        f"(interface 0, method 3); cannot resolve child '{child_name}'"
      )

    for i in range(parent_info.subobject_count):
      sub_addr, sub_info = await _subobject_address_and_info(introspection, parent_addr, i)
      sub_info.children = {}
      child_path = f"{parent_path}.{sub_info.name}"
      parent_info.children[sub_info.name] = sub_info
      self.register(child_path, sub_info)
      if sub_info.name == child_name:
        return sub_info.address

    raise KeyError(f"Child '{child_name}' not found under '{parent_path}'")


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
  """Structured firmware tree produced by introspection traversal."""

  roots: List[FirmwareTreeNode] = field(default_factory=list)

  def format(self) -> str:
    if not self.roots:
      return "<empty firmware tree>"
    lines: List[str] = []
    for idx, root in enumerate(self.roots):
      root_is_last = idx == len(self.roots) - 1
      lines.extend(root.format_lines(prefix="", is_last=root_is_last, is_root=True))
    return "\n".join(lines)

  def __str__(self) -> str:
    return self.format()


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
    return self.type_id in _ALL_COMPLEX_TYPE_IDS

  @property
  def is_struct_ref(self) -> bool:
    """True if this is a struct reference (type 30 in struct context, 57/61 in method context)."""
    return self.type_id in _STRUCT_REF_TYPE_IDS

  @property
  def is_enum_ref(self) -> bool:
    """True if this is an enum reference (type 32 in struct context, 78/81/82/85 in method)."""
    return self.type_id in _ENUM_REF_TYPE_IDS

  def resolve_name(
    self,
    registry: Optional["TypeRegistry"] = None,
    ho_interface_id: Optional[int] = None,
  ) -> str:
    """Resolve to a human-readable name, optionally using a TypeRegistry.

    For source_id=2 (local) refs, pass ``ho_interface_id`` (the HOI interface id
    of the method or struct owning this type) so resolution uses that interface's
    table only.
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
          "not in HoiObject mHoiParamTypes grid — update _HOI_TYPE_ROWS or add an override."
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

  Prefer :attr:`~pylabrobot.hamilton.tcp.client.HamiltonTCPClient.introspection`
  over constructing this class directly.
  """

  def __init__(self, backend: HamiltonTCPIntrospectionBackend):
    """Initialize introspection API.

    Args:
      backend: Session implementing :class:`HamiltonTCPIntrospectionBackend`
    """
    self.backend = backend
    # Session caches (invalidated when the client drops the introspection facet, e.g. reconnect).
    self._method_table_by_address: Dict[Address, List[MethodInfo]] = {}
    self._structs_by_addr_iface: Dict[Tuple[Address, int], Dict[int, StructInfo]] = {}
    self._enums_by_addr_iface: Dict[Tuple[Address, int], Dict[int, EnumInfo]] = {}
    self._iface_types_loaded: Set[Tuple[Address, int]] = set()
    self._interfaces_by_address: Dict[Address, List[InterfaceInfo]] = {}
    self._hc_result_text_by_addr_iface: Dict[Tuple[Address, int], Dict[int, str]] = {}
    self._supported_i0_by_address: Dict[Address, Set[int]] = {}
    self._global_type_pool_singleton: Optional[GlobalTypePool] = None
    self._firmware_tree_cache: Optional[FirmwareTree] = None

  def clear_session_caches(self) -> None:
    """Drop cached method tables, per-interface structs/enums, and the global type pool."""
    self._method_table_by_address.clear()
    self._structs_by_addr_iface.clear()
    self._enums_by_addr_iface.clear()
    self._iface_types_loaded.clear()
    self._interfaces_by_address.clear()
    self._hc_result_text_by_addr_iface.clear()
    self._supported_i0_by_address.clear()
    self._global_type_pool_singleton = None
    self._firmware_tree_cache = None

  def _attach_iface_types_to_registry(
    self, registry: TypeRegistry, addr: Address, iface_id: int
  ) -> None:
    """Copy cached structs/enums for (addr, iface_id) into *registry*."""
    key = (addr, iface_id)
    if key in self._structs_by_addr_iface:
      registry.structs[iface_id] = dict(self._structs_by_addr_iface[key])
    if key in self._enums_by_addr_iface:
      registry.enums[iface_id] = dict(self._enums_by_addr_iface[key])

  async def _ensure_parameter_types_for_signature(
    self,
    addr: Address,
    method: MethodInfo,
    registry: TypeRegistry,
  ) -> None:
    """Load structs/enums needed to resolve *method* signatures (recursive struct walk)."""
    seen_structs: Set[Tuple[int, int]] = set()
    max_nodes = 256

    async def walk(types: List[ParameterType], ho_iface: int) -> None:
      for pt in types:
        if not pt.is_complex or pt.source_id is None or pt.ref_id is None:
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
    if key in self._iface_types_loaded:
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
    self._structs_by_addr_iface[key] = structs_map
    self._enums_by_addr_iface[key] = enums_map
    hc_result = next((e for e in enums_map.values() if e.name == "HcResult"), None)
    if hc_result is not None:
      self._hc_result_text_by_addr_iface[key] = {int(v): n for n, v in hc_result.values.items()}
    else:
      self._hc_result_text_by_addr_iface[key] = {}
    self._iface_types_loaded.add(key)

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
    if key not in self._iface_types_loaded:
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
      else list(self.backend.global_object_addresses)
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
    """Resolve Address or dot-path through the backend resolver consistently."""
    if isinstance(addr_or_path, str):
      return cast(Address, await self.backend.resolve_path(addr_or_path))
    return addr_or_path

  async def _build_firmware_tree(self) -> FirmwareTree:
    """Build a DFS firmware tree from discovered root addresses."""
    roots = self.backend.get_root_object_addresses()
    tree = FirmwareTree()
    if not roots:
      return tree

    visited: Set[Address] = set()

    async def walk(addr: Address, path: Optional[str] = None) -> Optional[FirmwareTreeNode]:
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

      self.backend.registry.register(path, obj)

      # Keep this guard even though Interface-0 method 3 (GetSubobjectAddress)
      # appears ubiquitous in current PREP captures. If this remains stable
      # across instruments/firmware, we can consider relaxing this check later.
      if GET_SUBOBJECT_ADDRESS not in supported:
        return node

      for i in range(obj.subobject_count):
        try:
          sub_addr, sub_obj = await _subobject_address_and_info(self, addr, i)
          obj.children[sub_obj.name] = sub_obj
          child_path = f"{path}.{sub_obj.name}"
          child = await walk(sub_addr, child_path)
          if child is not None:
            node.children.append(child)
        except _TRANSIENT_ERRORS:
          raise
        except Exception as e:
          logger.debug("walk child failed for %s idx=%d: %s", addr, i, e)
      return node

    for addr in roots:
      root_node = await walk(addr)
      if root_node is not None:
        tree.roots.append(root_node)
    return tree

  async def get_firmware_tree(self, refresh: bool = False) -> FirmwareTree:
    """Return cached firmware tree, or build and cache it when missing."""
    if not refresh and self._firmware_tree_cache is not None:
      return self._firmware_tree_cache

    self._firmware_tree_cache = await self._build_firmware_tree()
    return self._firmware_tree_cache

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
