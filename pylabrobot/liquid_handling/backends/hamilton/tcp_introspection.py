"""Hamilton TCP Introspection API.

This module provides dynamic discovery of Hamilton instrument capabilities
using Interface 0 introspection methods. It allows discovering available
objects, methods, interfaces, enums, and structs at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pylabrobot.liquid_handling.backends.hamilton.protocol import HamiltonProtocol, HamiltonDataType
from pylabrobot.liquid_handling.backends.hamilton.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.messages import HoiParams, HoiParamsParser

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
    parameter_name: Optional[str] = None  # Parameter name (string)
    return_name: Optional[str] = None     # Return name (string)

    def get_signature_string(self) -> str:
        """Get method signature as a readable string."""
        param_str = self.parameter_name if self.parameter_name else "void"
        return_str = self.return_name if self.return_name else "void"
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
        field_strs = [f"{field_name}: {resolve_type_id(type_id)}"
                     for field_name, type_id in self.fields.items()]
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
        self.object_address = object_address

    def build_parameters(self) -> HoiParams:
        """Build parameters for get_object command."""
        # No parameters needed for get_object
        return HoiParams()

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
            'name': name,
            'version': version,
            'method_count': method_count,
            'subobject_count': subobject_count
        }


class GetMethodCommand(HamiltonCommand):
    """Get method signature (command_id=2)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 0
    command_id = 2
    action_code = 0  # QUERY

    def __init__(self, object_address: Address, method_index: int):
        super().__init__(object_address)
        self.object_address = object_address
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

        # The remaining fragments are STRINGs, not u8_arrays
        # First STRING after method name is parameter name (if any)
        # Second STRING is return name (if any)
        parameter_name = None
        return_name = None

        if parser.has_remaining():
            _, parameter_name = parser.parse_next()

        if parser.has_remaining():
            _, return_name = parser.parse_next()

        return {
            'interface_id': interface_id,
            'call_type': call_type,
            'method_id': method_id,
            'name': name,
            'parameter_name': parameter_name,  # String name, not type ID
            'return_name': return_name,       # String name, not type ID
        }


class GetSubobjectAddressCommand(HamiltonCommand):
    """Get subobject address (command_id=3)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 0
    command_id = 3
    action_code = 0  # QUERY

    def __init__(self, object_address: Address, subobject_index: int):
        super().__init__(object_address)
        self.object_address = object_address
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

        return {
            'address': Address(module_id, node_id, object_id)
        }


class GetInterfacesCommand(HamiltonCommand):
    """Get available interfaces (command_id=4)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 0
    command_id = 4
    action_code = 0  # QUERY

    def __init__(self, object_address: Address):
        super().__init__(object_address)
        self.object_address = object_address

    def build_parameters(self) -> HoiParams:
        """Build parameters for get_interfaces command."""
        # No parameters needed
        return HoiParams()

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
            interfaces.append({
                'interface_id': interface_id,
                'name': name,
                'version': version
            })

        return {'interfaces': interfaces}


class GetEnumsCommand(HamiltonCommand):
    """Get enum definitions (command_id=5)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 0
    command_id = 5
    action_code = 0  # QUERY

    def __init__(self, object_address: Address, target_interface_id: int):
        super().__init__(object_address)
        self.object_address = object_address
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

            enums.append({
                'enum_id': enum_id,
                'name': name,
                'values': values
            })

        return {'enums': enums}


class GetStructsCommand(HamiltonCommand):
    """Get struct definitions (command_id=6)."""

    protocol = HamiltonProtocol.OBJECT_DISCOVERY
    interface_id = 0
    command_id = 6
    action_code = 0  # QUERY

    def __init__(self, object_address: Address, target_interface_id: int):
        super().__init__(object_address)
        self.object_address = object_address
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

            structs.append({
                'struct_id': struct_id,
                'name': name,
                'fields': fields
            })

        return {'structs': structs}


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
            name=response['name'],
            version=response['version'],
            method_count=response['method_count'],
            subobject_count=response['subobject_count'],
            address=address
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
            interface_id=response['interface_id'],
            call_type=response['call_type'],
            method_id=response['method_id'],
            name=response['name'],
            parameter_name=response.get('parameter_name'),
            return_name=response.get('return_name')
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

        return response['address']

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
                interface_id=iface['interface_id'],
                name=iface['name'],
                version=iface['version']
            )
            for iface in response['interfaces']
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
            EnumInfo(
                enum_id=enum_def['enum_id'],
                name=enum_def['name'],
                values=enum_def['values']
            )
            for enum_def in response['enums']
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
                struct_id=struct_def['struct_id'],
                name=struct_def['name'],
                fields=struct_def['fields']
            )
            for struct_def in response['structs']
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
            hierarchy['info'] = root_info

            # Discover subobjects
            subobjects = {}
            for i in range(root_info.subobject_count):
                try:
                    subaddress = await self.get_subobject_address(root_address, i)
                    subobjects[f'subobject_{i}'] = await self.discover_hierarchy(subaddress)
                except Exception as e:
                    logger.warning(f"Failed to discover subobject {i}: {e}")

            hierarchy['subobjects'] = subobjects

            # Discover methods
            methods = await self.get_all_methods(root_address)
            hierarchy['methods'] = methods

        except Exception as e:
            logger.error(f"Failed to discover hierarchy for {root_address}: {e}")
            hierarchy['error'] = str(e)

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
                all_objects[str(root_address)] = {'error': str(e)}

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

    def get_methods_by_interface(self, methods: List[MethodInfo], interface_id: int) -> List[MethodInfo]:
        """Filter methods by interface ID.

        Args:
            methods: List of MethodInfo objects to filter
            interface_id: Interface ID to filter by

        Returns:
            List of methods from the specified interface
        """
        return [method for method in methods if method.interface_id == interface_id]
