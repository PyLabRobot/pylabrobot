"""Canonical v1 Hamilton TCP namespace."""

from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.hoi_error import (
  HoiError,
  parse_hamilton_error_entries,
  parse_hamilton_error_entry,
  parse_hamilton_error_params,
)
from pylabrobot.hamilton.tcp.interface_bundle import InterfacePathSpec, resolve_interface_path_specs
from pylabrobot.hamilton.tcp.introspection import (
  Direction,
  FirmwareTreeNode,
  MethodInfo,
  MethodParamType,
  ObjectInfo,
  StructFieldType,
  flatten_firmware_tree,
)
from pylabrobot.hamilton.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParams,
  HoiParamsParser,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
)
from pylabrobot.hamilton.tcp.packets import (
  Address,
  ConnectionPacket,
  HarpPacket,
  HoiPacket,
  IpPacket,
  RegistrationPacket,
)
from pylabrobot.hamilton.tcp.protocol import (
  HAMILTON_PROTOCOL_VERSION_MAJOR,
  HAMILTON_PROTOCOL_VERSION_MINOR,
  HamiltonProtocol,
  HarpTransportableProtocol,
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)
from pylabrobot.hamilton.tcp.wire_types import HamiltonDataType

__all__ = [
  "Address",
  "InterfacePathSpec",
  "resolve_interface_path_specs",
  "CommandMessage",
  "CommandResponse",
  "ConnectionPacket",
  "Direction",
  "FirmwareTreeNode",
  "MethodParamType",
  "StructFieldType",
  "flatten_firmware_tree",
  "HAMILTON_PROTOCOL_VERSION_MAJOR",
  "HAMILTON_PROTOCOL_VERSION_MINOR",
  "HoiError",
  "parse_hamilton_error_entries",
  "parse_hamilton_error_entry",
  "parse_hamilton_error_params",
  "TCPCommand",
  "HamiltonTCPClient",
  "HamiltonDataType",
  "HamiltonProtocol",
  "HarpPacket",
  "HarpTransportableProtocol",
  "Hoi2Action",
  "HoiPacket",
  "HoiParams",
  "HoiParamsParser",
  "HoiRequestId",
  "InitMessage",
  "InitResponse",
  "IpPacket",
  "MethodInfo",
  "ObjectInfo",
  "RegistrationActionCode",
  "RegistrationMessage",
  "RegistrationOptionType",
  "RegistrationPacket",
  "RegistrationResponse",
]
