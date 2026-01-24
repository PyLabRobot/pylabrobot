"""Hamilton TCP Introspection API.

This module provides dynamic discovery of Hamilton instrument capabilities
using Interface 0 introspection methods. It allows discovering available
objects, methods, interfaces, enums, and structs at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams, HoiParamsParser
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonDataType,
  HamiltonProtocol,
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


def resolve_type_ids(type_ids: List[int]) -> List[str]:
  """Resolve list of Hamilton type IDs to readable names.

  Args:
      type_ids: List of Hamilton data type IDs

  Returns:
      List of human-readable type names
  """
  return [resolve_type_id(tid) for tid in type_ids]


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
  61: "List[struct]",  # Complex type, needs source_id + struct_id
  66: "List[bool]",
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
_ARGUMENT_TYPE_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 33, 41, 45, 49, 53, 61, 66, 82, 102}
_RETURN_ELEMENT_TYPE_IDS = {18, 19, 20, 21, 22, 23, 24, 35, 43, 47, 51, 55, 68, 76}
_RETURN_VALUE_TYPE_IDS = {25, 26, 27, 28, 29, 30, 31, 32, 36, 44, 48, 52, 56, 69, 81, 85, 104, 105}
_COMPLEX_TYPE_IDS = {60, 61, 64, 78, 81, 82, 85}  # Types that need additional bytes


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


def is_complex_introspection_type(type_id: int) -> bool:
  """Check if introspection type is complex (needs additional bytes).

  Complex types require 3 bytes total: type_id, source_id, struct_id/enum_id

  Args:
      type_id: Introspection type ID

  Returns:
      True if type is complex
  """
  return type_id in _COMPLEX_TYPE_IDS


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


@dataclass
class MethodInfo:
  """Method signature from introspection."""

  interface_id: int
  call_type: int
  method_id: int
  name: str
  parameter_types: list[int] = field(
    default_factory=list
  )  # Decoded parameter type IDs (Argument category)
  parameter_labels: list[str] = field(default_factory=list)  # Parameter names (if available)
  return_types: list[int] = field(
    default_factory=list
  )  # Decoded return type IDs (ReturnElement/ReturnValue category)
  return_labels: list[str] = field(default_factory=list)  # Return names (if available)

  def get_signature_string(self) -> str:
    """Get method signature as a readable string."""
    # Decode parameter types to readable names
    if self.parameter_types:
      param_type_names = [resolve_introspection_type_name(tid) for tid in self.parameter_types]

      # If we have labels, use them; otherwise just show types
      if self.parameter_labels and len(self.parameter_labels) == len(param_type_names):
        # Format as "param1: type1, param2: type2"
        params = [
          f"{label}: {type_name}"
          for label, type_name in zip(self.parameter_labels, param_type_names)
        ]
        param_str = ", ".join(params)
      else:
        # Just show types
        param_str = ", ".join(param_type_names)
    else:
      param_str = "void"

    # Decode return types to readable names
    if self.return_types:
      return_type_names = [resolve_introspection_type_name(tid) for tid in self.return_types]
      return_categories = [get_introspection_type_category(tid) for tid in self.return_types]

      # Format return based on category
      if any(cat == "ReturnElement" for cat in return_categories):
        # Multiple return values → struct format
        if self.return_labels and len(self.return_labels) == len(return_type_names):
          # Format as "{ label1: type1, label2: type2 }"
          returns = [
            f"{label}: {type_name}"
            for label, type_name in zip(self.return_labels, return_type_names)
          ]
          return_str = f"{{ {', '.join(returns)} }}"
        else:
          # Just show types
          return_str = f"{{ {', '.join(return_type_names)} }}"
      elif len(return_type_names) == 1:
        # Single return value
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

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_object response."""
    # Parse HOI2 DataFragments
    parser = HoiParamsParser(data)

    _, name = parser.parse_next()
    _, version = parser.parse_next()
    _, method_count = parser.parse_next()
    _, subobject_count = parser.parse_next()

    return {
      "name": name,
      "version": version,
      "method_count": method_count,
      "subobject_count": subobject_count,
    }


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
    return HoiParams().u32(self.method_index)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_method response."""
    parser = HoiParamsParser(data)

    _, interface_id = parser.parse_next()
    _, call_type = parser.parse_next()
    _, method_id = parser.parse_next()
    _, name = parser.parse_next()

    # The remaining fragments are STRING types containing type IDs as bytes
    # Hamilton sends ONE combined list where type IDs encode category (Argument/ReturnElement/ReturnValue)
    # First STRING after method name is parameter_types (each byte is a type ID - can be Argument or Return)
    # Second STRING (if present) is parameter_labels (comma-separated names - includes both params and returns)
    parameter_types_str = None
    parameter_labels_str = None

    if parser.has_remaining():
      _, parameter_types_str = parser.parse_next()

    if parser.has_remaining():
      _, parameter_labels_str = parser.parse_next()

    # Decode string bytes to type IDs (like piglet does: .as_bytes().to_vec())
    all_type_ids: list[int] = []
    if parameter_types_str:
      all_type_ids = [ord(c) for c in parameter_types_str]

    # Parse all labels (comma-separated - includes both parameters and returns)
    all_labels: list[str] = []
    if parameter_labels_str:
      all_labels = [label.strip() for label in parameter_labels_str.split(",") if label.strip()]

    # Categorize by type ID ranges (like piglet does)
    # Split into arguments vs returns based on type ID category
    parameter_types: list[int] = []
    parameter_labels: list[str] = []
    return_types: list[int] = []
    return_labels: list[str] = []

    for i, type_id in enumerate(all_type_ids):
      category = get_introspection_type_category(type_id)
      label = all_labels[i] if i < len(all_labels) else None

      if category == "Argument":
        parameter_types.append(type_id)
        if label:
          parameter_labels.append(label)
      elif category in ("ReturnElement", "ReturnValue"):
        return_types.append(type_id)
        if label:
          return_labels.append(label)
      # Unknown types - could be parameters or returns, default to parameters
      else:
        parameter_types.append(type_id)
        if label:
          parameter_labels.append(label)

    return {
      "interface_id": interface_id,
      "call_type": call_type,
      "method_id": method_id,
      "name": name,
      "parameter_types": parameter_types,  # Decoded type IDs (Argument category only)
      "parameter_labels": parameter_labels,  # Parameter names only
      "return_types": return_types,  # Decoded type IDs (ReturnElement/ReturnValue only)
      "return_labels": return_labels,  # Return names only
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
    return HoiParams().u16(self.subobject_index)  # Use u16, not u32

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_subobject_address response."""
    parser = HoiParamsParser(data)

    _, module_id = parser.parse_next()
    _, node_id = parser.parse_next()
    _, object_id = parser.parse_next()

    return {"address": Address(module_id, node_id, object_id)}


class GetInterfacesCommand(HamiltonCommand):
  """Get available interfaces (command_id=4)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 0
  command_id = 4
  action_code = 0  # QUERY

  def __init__(self, object_address: Address):
    super().__init__(object_address)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_interfaces response."""
    parser = HoiParamsParser(data)

    interfaces = []
    _, interface_count = parser.parse_next()

    for _ in range(interface_count):
      _, interface_id = parser.parse_next()
      _, name = parser.parse_next()
      _, version = parser.parse_next()
      interfaces.append({"interface_id": interface_id, "name": name, "version": version})

    return {"interfaces": interfaces}


class GetEnumsCommand(HamiltonCommand):
  """Get enum definitions (command_id=5)."""

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

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_enums response."""
    parser = HoiParamsParser(data)

    enums = []
    _, enum_count = parser.parse_next()

    for _ in range(enum_count):
      _, enum_id = parser.parse_next()
      _, name = parser.parse_next()

      # Parse enum values
      _, value_count = parser.parse_next()
      values = {}
      for _ in range(value_count):
        _, value_name = parser.parse_next()
        _, value_value = parser.parse_next()
        values[value_name] = value_value

      enums.append({"enum_id": enum_id, "name": name, "values": values})

    return {"enums": enums}


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
    return HoiParams().u8(self.target_interface_id)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse get_structs response."""
    parser = HoiParamsParser(data)

    structs = []
    _, struct_count = parser.parse_next()

    for _ in range(struct_count):
      _, struct_id = parser.parse_next()
      _, name = parser.parse_next()

      # Parse struct fields
      _, field_count = parser.parse_next()
      fields = {}
      for _ in range(field_count):
        _, field_name = parser.parse_next()
        _, field_type = parser.parse_next()
        fields[field_name] = field_type

      structs.append({"struct_id": struct_id, "name": name, "fields": fields})

    return {"structs": structs}


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

  async def get_object(self, address: Address) -> ObjectInfo:
    """Get object metadata.

    Args:
      address: Object address to query

    Returns:
      Object metadata
    """
    command = GetObjectCommand(address)
    response = await self.backend.send_command(command)

    return ObjectInfo(
      name=response["name"],
      version=response["version"],
      method_count=response["method_count"],
      subobject_count=response["subobject_count"],
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
    response = await self.backend.send_command(command)

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
    response = await self.backend.send_command(command)

    # Type: ignore needed because response dict is typed as dict[str, Any]
    # but we know 'address' key contains Address object
    return response["address"]  # type: ignore[no-any-return, return-value]

  async def get_interfaces(self, address: Address) -> List[InterfaceInfo]:
    """Get available interfaces.

    Args:
      address: Object address

    Returns:
      List of interface information
    """
    command = GetInterfacesCommand(address)
    response = await self.backend.send_command(command)

    return [
      InterfaceInfo(
        interface_id=iface["interface_id"], name=iface["name"], version=iface["version"]
      )
      for iface in response["interfaces"]
    ]

  async def get_enums(self, address: Address, interface_id: int) -> List[EnumInfo]:
    """Get enum definitions.

    Args:
      address: Object address
      interface_id: Interface ID

    Returns:
      List of enum definitions
    """
    command = GetEnumsCommand(address, interface_id)
    response = await self.backend.send_command(command)

    return [
      EnumInfo(enum_id=enum_def["enum_id"], name=enum_def["name"], values=enum_def["values"])
      for enum_def in response["enums"]
    ]

  async def get_structs(self, address: Address, interface_id: int) -> List[StructInfo]:
    """Get struct definitions.

    Args:
      address: Object address
      interface_id: Interface ID

    Returns:
      List of struct definitions
    """
    command = GetStructsCommand(address, interface_id)
    response = await self.backend.send_command(command)

    return [
      StructInfo(
        struct_id=struct_def["struct_id"], name=struct_def["name"], fields=struct_def["fields"]
      )
      for struct_def in response["structs"]
    ]

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

  async def discover_hierarchy(self, root_address: Address) -> Dict[str, Any]:
    """Recursively discover object hierarchy.

    Args:
      root_address: Root object address

    Returns:
      Nested dictionary of discovered objects
    """
    hierarchy = {}

    try:
      # Get root object info
      root_info = await self.get_object(root_address)
      # Type: ignore needed because hierarchy is Dict[str, Any] for flexibility
      hierarchy["info"] = root_info  # type: ignore[assignment]

      # Discover subobjects
      subobjects = {}
      for i in range(root_info.subobject_count):
        try:
          subaddress = await self.get_subobject_address(root_address, i)
          subobjects[f"subobject_{i}"] = await self.discover_hierarchy(subaddress)
        except Exception as e:
          logger.warning(f"Failed to discover subobject {i}: {e}")

      # Type: ignore needed because hierarchy is Dict[str, Any] for flexibility
      hierarchy["subobjects"] = subobjects  # type: ignore[assignment]

      # Discover methods
      methods = await self.get_all_methods(root_address)
      # Type: ignore needed because hierarchy is Dict[str, Any] for flexibility
      hierarchy["methods"] = methods  # type: ignore[assignment]

    except Exception as e:
      logger.error(f"Failed to discover hierarchy for {root_address}: {e}")
      # Type: ignore needed because hierarchy is Dict[str, Any] for flexibility
      hierarchy["error"] = str(e)  # type: ignore[assignment]

    return hierarchy

  async def discover_all_objects(self, root_addresses: List[Address]) -> Dict[str, Any]:
    """Discover all objects starting from root addresses.

    Args:
      root_addresses: List of root addresses to start discovery from

    Returns:
      Dictionary mapping address strings to discovered hierarchies
    """
    all_objects = {}

    for root_address in root_addresses:
      try:
        hierarchy = await self.discover_hierarchy(root_address)
        all_objects[str(root_address)] = hierarchy
      except Exception as e:
        logger.error(f"Failed to discover objects from {root_address}: {e}")
        all_objects[str(root_address)] = {"error": str(e)}

    return all_objects

  def print_method_signatures(self, methods: List[MethodInfo]) -> None:
    """Print method signatures in a readable format.

    Args:
      methods: List of MethodInfo objects to print
    """
    print("Method Signatures:")
    print("=" * 50)
    for method in methods:
      print(f"  {method.get_signature_string()}")
      print(f"    Interface: {method.interface_id}, Method ID: {method.method_id}")
      print()

  def print_struct_definitions(self, structs: List[StructInfo]) -> None:
    """Print struct definitions in a readable format.

    Args:
      structs: List of StructInfo objects to print
    """
    print("Struct Definitions:")
    print("=" * 50)
    for struct in structs:
      print(struct.get_struct_string())
      print()

  def get_methods_by_name(self, methods: List[MethodInfo], name_pattern: str) -> List[MethodInfo]:
    """Filter methods by name pattern.

    Args:
      methods: List of MethodInfo objects to filter
      name_pattern: Name pattern to search for (case-insensitive)

    Returns:
      List of methods matching the name pattern
    """
    return [method for method in methods if name_pattern.lower() in method.name.lower()]

  def get_methods_by_interface(
    self, methods: List[MethodInfo], interface_id: int
  ) -> List[MethodInfo]:
    """Filter methods by interface ID.

    Args:
      methods: List of MethodInfo objects to filter
      interface_id: Interface ID to filter by

    Returns:
      List of methods from the specified interface
    """
    return [method for method in methods if method.interface_id == interface_id]
