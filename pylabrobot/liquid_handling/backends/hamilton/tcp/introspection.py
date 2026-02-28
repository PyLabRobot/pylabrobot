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
from typing import Annotated, Dict, List, Optional, Set, Union

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  HoiParams,
  HoiParamsParser,
  inspect_hoi_params,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  HamiltonDataType,
  I8Array,
  I32,
  I32Array,
  Str,
  StrArray,
  U8,
  U8Array,
  U16,
  U32,
  U32Array,
)

logger = logging.getLogger(__name__)

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
  66: "List[bool]",
  77: "List[str]",
  82: "List[enum]",  # Complex type, needs source_id + enum_id
  102: "f32",
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
_ARGUMENT_TYPE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 33, 41, 45, 49, 53, 57, 61, 66, 77, 82, 102}
_RETURN_ELEMENT_TYPE_IDS = {18, 19, 20, 21, 22, 23, 24, 35, 43, 47, 51, 55, 68, 76}
_RETURN_VALUE_TYPE_IDS = {25, 26, 27, 28, 29, 30, 31, 32, 36, 44, 48, 52, 56, 69, 81, 85, 104, 105}
_COMPLEX_TYPE_IDS = {57, 60, 61, 64, 78, 81, 82, 85}  # Types that need source_id + ref_id

# HC_RESULT codes returned in COMMAND_EXCEPTION / STATUS_EXCEPTION (extend as observed)
_HC_RESULT_DESCRIPTIONS: Dict[int, str] = {
  0x0000: "success",
  0x0005: "invalid parameter / not supported",
  0x0006: "unknown command",
  0x0200: "hardware error",
  0x020A: "hardware not ready / axis error",
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
  """A resolved type reference from a method signature.

  Simple types (i8, f32, etc.) have only type_id set.
  Complex types (struct, enum, List[struct], List[enum]) additionally have
  source_id (the interface defining the struct/enum) and ref_id (struct_id
  or enum_id within that interface). These are encoded as 3-byte triples
  [type_id, source_id, ref_id] in the GetMethod response.
  """

  type_id: int
  source_id: Optional[int] = None
  ref_id: Optional[int] = None

  @property
  def is_complex(self) -> bool:
    return self.type_id in _COMPLEX_TYPE_IDS

  def resolve_name(self, registry: Optional["TypeRegistry"] = None) -> str:
    """Resolve to a human-readable name, optionally using a TypeRegistry."""
    base = resolve_introspection_type_name(self.type_id)
    if not self.is_complex or self.source_id is None or self.ref_id is None:
      return base
    if registry is None:
      return f"{base}(iface={self.source_id}, id={self.ref_id})"
    if "struct" in base.lower():
      s = registry.resolve_struct(self.source_id, self.ref_id)
      return s.name if s else f"{base}(iface={self.source_id}, id={self.ref_id})"
    if "enum" in base.lower():
      e = registry.resolve_enum(self.source_id, self.ref_id)
      return e.name if e else f"{base}(iface={self.source_id}, id={self.ref_id})"
    return f"{base}(iface={self.source_id}, id={self.ref_id})"


def _parse_type_ids(raw: str) -> List[ParameterType]:
  """Parse the parameter_types string from GetMethod into ParameterType list.

  Simple types are 1 byte each. Complex types (struct, enum references) are
  3-byte triples: [type_id, source_id, ref_id]. The _COMPLEX_TYPE_IDS set
  identifies which type_ids consume 3 bytes.
  """
  data = [ord(c) for c in raw] if raw else []
  result: List[ParameterType] = []
  i = 0
  while i < len(data):
    tid = data[i]
    if tid in _COMPLEX_TYPE_IDS and i + 2 < len(data):
      result.append(ParameterType(tid, source_id=data[i + 1], ref_id=data[i + 2]))
      i += 3
    else:
      result.append(ParameterType(tid))
      i += 1
  return result


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
    if self.parameter_types:
      param_type_names = [pt.resolve_name(registry) for pt in self.parameter_types]
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
      return_type_names = [rt.resolve_name(registry) for rt in self.return_types]
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

    return f"{self.name}({param_str}) -> {return_str}"


@dataclass
class TypeRegistry:
  """Resolved type information for one object.

  Built once from introspection during setup. Caches structs, enums, and
  interface info so method signatures can be fully resolved without additional
  device calls. Use build_type_registry() to create.

  Example:
    registry = await intro.build_type_registry(mph_addr)
    method = registry.get_method(interface_id=1, method_id=9)
    print(method.get_signature_string(registry))  # PickupTips(tipParameters: PickupTipParameters, ...)
  """

  address: Address
  interfaces: Dict[int, "InterfaceInfo"] = field(default_factory=dict)
  structs: Dict[int, Dict[int, "StructInfo"]] = field(default_factory=dict)
  enums: Dict[int, Dict[int, "EnumInfo"]] = field(default_factory=dict)
  methods: List[MethodInfo] = field(default_factory=list)

  def resolve_struct(self, interface_id: int, struct_id: int) -> Optional["StructInfo"]:
    """Look up a struct by interface_id and struct_id."""
    return self.structs.get(interface_id, {}).get(struct_id)

  def resolve_enum(self, interface_id: int, enum_id: int) -> Optional["EnumInfo"]:
    """Look up an enum by interface_id and enum_id."""
    return self.enums.get(interface_id, {}).get(enum_id)

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
  """Struct definition from introspection."""

  struct_id: int
  name: str
  fields: Dict[str, int]  # field_name -> type_id

  @property
  def field_type_names(self) -> Dict[str, str]:
    """Get human-readable field type names."""
    return {field_name: resolve_type_id(type_id) for field_name, type_id in self.fields.items()}

  def get_struct_string(self) -> str:
    """Get struct definition as a readable string."""
    field_strs = [
      f"{field_name}: {resolve_type_id(type_id)}" for field_name, type_id in self.fields.items()
    ]
    fields_str = "\n  ".join(field_strs) if field_strs else "  (empty)"
    return f"struct {self.name} {{\n  {fields_str}\n}}"


# GetStructs wire format (device sends 4 separate array fragments, not count+rows):
# [0] STRING_ARRAY = struct names (one per struct)
# [1] U32_ARRAY   = struct IDs
# [2] U8_ARRAY    = field type IDs (flat across all structs)
# [3] STRING_ARRAY = field names (flat across all structs)
# Fields are split across structs by dividing evenly (e.g. 7 fields, 2 structs -> 3 + 4).


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
    method_count: I32
    subobject_count: I32


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
    parameter_types_str = None
    parameter_labels_str = None

    if parser.has_remaining():
      _, parameter_types_str = parser.parse_next()

    if parser.has_remaining():
      _, parameter_labels_str = parser.parse_next()

    all_types = _parse_type_ids(parameter_types_str) if parameter_types_str else []

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
    """GetStructs returns 4 fragments: struct names, struct IDs, field type IDs, field names."""

    struct_names: StrArray
    struct_ids: U32Array
    field_type_ids: U8Array
    field_names: StrArray


# ============================================================================
# HIGH-LEVEL INTROSPECTION API
# ============================================================================


class HamiltonIntrospection:
  """High-level API for Hamilton introspection."""

  def __init__(self, backend):
    """Initialize introspection API.

    Args:
      backend: TCPBackend instance
    """
    self.backend = backend

  def _resolve_address(self, addr_or_path: Union[Address, str]) -> Address:
    """Resolve dot-path string to Address using the backend's registry, or return Address as-is."""
    if isinstance(addr_or_path, str):
      return self.backend._registry.address(addr_or_path)
    return addr_or_path

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

  async def get_interfaces(self, address: Address) -> List[InterfaceInfo]:
    """Get available interfaces.

    The device returns 2 columnar fragments: interface_ids (I8_ARRAY) and
    interface_names (STRING_ARRAY).

    Args:
      address: Object address

    Returns:
      List of interface information
    """
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

  async def get_structs_raw(
    self, address: Address, interface_id: int
  ) -> tuple[bytes, List[dict]]:
    """Get raw GetStructs response bytes and a fragment-by-fragment breakdown.

    Use this to see exactly what the device sends so response parsing can
    match the wire format. Returns (params_bytes, inspect_hoi_params(params)).

    Example:
      raw, fragments = await intro.get_structs_raw(mph_addr, 1)
      for i, f in enumerate(fragments):
        print(f\"{i}: type_id={f['type_id']} len={f['length']} decoded={f['decoded']!r}\")
    """
    command = GetStructsCommand(address, interface_id)
    result = await self.backend.send_command(
      command, ensure_connection=False, return_raw=True
    )
    (params,) = result
    return params, inspect_hoi_params(params)

  async def get_structs(self, address: Address, interface_id: int) -> List[StructInfo]:
    """Get struct definitions.

    The device returns 4 fragments: struct_names (StrArray), struct_ids (U32Array),
    field_type_ids (U8Array), field_names (StrArray). Fields are split across
    structs in order (even split when not divisible).

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
    struct_ids = list(response.struct_ids)
    field_type_ids = list(response.field_type_ids)
    field_names = list(response.field_names)
    n_structs = len(struct_ids)
    n_fields = len(field_names)
    if n_structs == 0:
      return []
    # Split field names/type IDs across structs (even split)
    counts = [
      n_fields // n_structs + (1 if i < n_fields % n_structs else 0)
      for i in range(n_structs)
    ]
    offset = 0
    result: List[StructInfo] = []
    for i in range(n_structs):
      cnt = counts[i]
      name = struct_names[i] if i < len(struct_names) else f"Struct_{struct_ids[i]}"
      # field_type_ids may have one extra (e.g. 8 for 7 names); use min to stay in range
      types_slice = field_type_ids[offset : offset + cnt]
      names_slice = field_names[offset : offset + cnt]
      fields = dict(zip(names_slice, types_slice))
      result.append(
        StructInfo(struct_id=int(struct_ids[i]), name=name, fields=fields)
      )
      offset += cnt
    return result

  async def get_all_methods(self, address: Address) -> List[MethodInfo]:
    """Get all methods for an object.

    Args:
      address: Object address

    Returns:
      List of all method signatures
    """
    # First get object info to know how many methods there are
    object_info = await self.get_object(address)

    methods = []
    for i in range(object_info.method_count):
      try:
        method = await self.get_method(address, i)
        methods.append(method)
      except Exception as e:
        logger.warning(f"Failed to get method {i} for {address}: {e}")

    return methods

  async def build_type_registry(self, address: Union[Address, str]) -> TypeRegistry:
    """Build a complete TypeRegistry for an object.

    Uses InterfaceDescriptors (get_interfaces) as the canonical source of
    interface IDs; then queries structs and enums only for those interfaces.
    No probing or fallback from method-derived interface IDs.

    Args:
      address: Object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").

    Returns:
      TypeRegistry with all type information for this object
    """
    address = self._resolve_address(address)
    registry = TypeRegistry(address=address)

    # Canonical interface list — InterfaceDescriptors (command id=4)
    interfaces = await self.get_interfaces(address)
    for iface in interfaces:
      registry.interfaces[iface.interface_id] = iface

    # Methods — query separately; don't use for interface discovery
    registry.methods = await self.get_all_methods(address)

    # Structs + enums for each declared interface
    for iface in interfaces:
      structs = await self.get_structs(address, iface.interface_id)
      if structs:
        registry.structs[iface.interface_id] = {s.struct_id: s for s in structs}
      enums = await self.get_enums(address, iface.interface_id)
      if enums:
        registry.enums[iface.interface_id] = {e.enum_id: e for e in enums}

    return registry

  async def build_type_registry_with_children(
    self,
    address: Union[Address, str],
    subobject_addresses: Optional[List[Address]] = None,
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

    Returns:
      TypeRegistry that can resolve types from both parent and children.
    """
    address = self._resolve_address(address)
    registry = await self.build_type_registry(address)

    if subobject_addresses is None:
      obj_info = await self.get_object(address)
      subobject_addresses = []
      for i in range(obj_info.subobject_count):
        try:
          sub_addr = await self.get_subobject_address(address, i)
          subobject_addresses.append(sub_addr)
        except Exception:
          logger.debug("get_subobject_address(%d) failed for %s", i, address)

    for sub_addr in subobject_addresses:
      try:
        child_reg = await self.build_type_registry(sub_addr)
        for iid, struct_map in child_reg.structs.items():
          registry.structs.setdefault(iid, {}).update(struct_map)
        for iid, enum_map in child_reg.enums.items():
          registry.enums.setdefault(iid, {}).update(enum_map)
      except Exception as e:
        logger.debug("build_type_registry failed for child %s: %s", sub_addr, e)

    return registry

  async def get_method_by_id(
    self,
    address: Union[Address, str],
    interface_id: int,
    method_id: int,
  ) -> Optional[MethodInfo]:
    """Return the method with the given interface_id and method_id (action id).

    Use this when you get a COMMAND_EXCEPTION to see the expected parameter
    names and types for the command that was rejected. Example::

      intro = HamiltonIntrospection(backend.client)
      method = await intro.get_method_by_id(mph_address, interface_id=1, method_id=9)
      if method:
        print("Expected parameters:", method.parameter_labels)
        print("Signature:", method.get_signature_string())

    Args:
      address: Object address or dot-path (e.g. "MLPrepRoot.MphRoot.MPH").
      interface_id: Interface ID (e.g. 1 for IChannel/IMph).
      method_id: Method/command ID (e.g. 9 for PickupTips).

    Returns:
      MethodInfo for the matching method, or None if not found.
    """
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
          resolved = pt.resolve_name(registry)
          lines.append(f"  Param type {pt.type_id}: {resolved}")
    else:
      lines.append(f"  Method [{interface_id}:{method_id}] <not found>")

    if hc_result is not None:
      lines.append(f"  Device error: {describe_hc_result(hc_result)}")
    elif error_text:
      lines.append(f"  Device said: {error_text}")

    return "\n".join(lines)

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
    return await self.resolve_error(
      address, interface_id, method_id,
      registry=registry, hc_result=hc_result,
    )
