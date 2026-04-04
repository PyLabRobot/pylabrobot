"""Compatibility shim — canonical location is pylabrobot.hamilton.tcp.messages."""

from pylabrobot.hamilton.tcp.messages import (  # noqa: F401
  CommandMessage,
  CommandResponse,
  HoiParams,
  HoiParamsParser,
  InitMessage,
  InitResponse,
  RegistrationMessage,
  RegistrationResponse,
)
