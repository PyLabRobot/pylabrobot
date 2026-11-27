"""One class per command, grouped by what the command is for."""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.serialization.commands.configuration import (
  ByteQuery,
  FlagQuery,
  GetSyringeBoxInfo,
  SelectorQuery,
  SelectorWrite,
  SyringeBox,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.diagnostics import (
  HomeVerifyMotors,
  ResetInstrument,
  RunSelfCheck,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.queries import (
  FirmwareVersion,
  GetFirmwareVersion,
  GetSerialNumber,
  Ping,
)
from pylabrobot.agilent.biotek.lhc.serialization.commands.run_control import (
  AbortStep,
  ExitProtocol,
  GetProtocolStatus,
  InitProtocol,
  PauseStep,
  ResumeStep,
  RunStatus,
  RunStep,
)

__all__ = [
  "AbortStep",
  "ByteQuery",
  "ExitProtocol",
  "FirmwareVersion",
  "FlagQuery",
  "GetFirmwareVersion",
  "GetProtocolStatus",
  "GetSerialNumber",
  "GetSyringeBoxInfo",
  "HomeVerifyMotors",
  "InitProtocol",
  "PauseStep",
  "Ping",
  "ResetInstrument",
  "ResumeStep",
  "RunSelfCheck",
  "RunStatus",
  "RunStep",
  "SelectorQuery",
  "SelectorWrite",
  "SyringeBox",
]
