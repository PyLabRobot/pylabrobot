"""Compatibility shim — canonical location is pylabrobot.hamilton.tcp.protocol."""

from pylabrobot.hamilton.tcp.protocol import (  # noqa: F401
  HAMILTON_PROTOCOL_VERSION_MAJOR,
  HAMILTON_PROTOCOL_VERSION_MINOR,
  HamiltonDataType,
  HamiltonProtocol,
  HarpTransportableProtocol,
  Hoi2Action,
  HoiRequestId,
  RegistrationActionCode,
  RegistrationOptionType,
)
