"""Compatibility shim — canonical location is pylabrobot.hamilton.tcp.introspection."""

from pylabrobot.hamilton.tcp.introspection import (  # noqa: F401
  EnumInfo,
  GetEnumsCommand,
  GetInterfacesCommand,
  GetMethodCommand,
  GetObjectCommand,
  GetStructsCommand,
  GetSubobjectAddressCommand,
  HamiltonIntrospection,
  InterfaceInfo,
  MethodInfo,
  ObjectInfo,
  StructInfo,
  get_introspection_type_category,
  is_complex_introspection_type,
  resolve_introspection_type_name,
  resolve_type_id,
  resolve_type_ids,
)
