"""ODTC-specific SiLA interface with parallelism, state management, and lockId validation.

This module extends InhecoSiLAInterface to support ODTC-specific requirements:
- Multiple in-flight commands with parallelism enforcement
- State machine tracking and command allowability checks
- LockId validation (defaults to None, validates when device is locked)
- Proper return code handling (including device-specific codes)
- All event types (ResponseEvent, StatusEvent, DataEvent, ErrorEvent)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from pylabrobot.storage.inheco.scila.inheco_sila_interface import (
  InhecoSiLAInterface,
  SiLAState,
)

from .odtc_model import ODTCProgress

# -----------------------------------------------------------------------------
# SiLA/ODTC exceptions (typed command and device errors)
# -----------------------------------------------------------------------------


class SiLAError(RuntimeError):
  """Base exception for SiLA command and device errors. Use .code for return-code-specific handling (4=busy, 5=lock, 6=requestId, 9=state, 11=parameter, 1000+=device)."""

  def __init__(self, msg: str, code: Optional[int] = None) -> None:
    super().__init__(msg)
    self.code = code


class SiLATimeoutError(SiLAError):
  """Command timed out: lifetime_of_execution exceeded or ResponseEvent not received."""

  pass


class FirstEventTimeout(SiLAError):
  """No first event received within timeout (e.g. no DataEvent for ExecuteMethod)."""

  pass


SOAP_RESPONSE_ErrorEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <ErrorEventResponse xmlns="http://sila.coop">
      <ErrorEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0005967S</duration>
        <deviceClass>0</deviceClass>
      </ErrorEventResult>
    </ErrorEventResponse>
  </s:Body>
</s:Envelope>"""


# Default max wait for async command completion (3 hours). SiLA2-aligned: protocol execution always bounded.
DEFAULT_LIFETIME_OF_EXECUTION: float = 10800.0

# Default timeout for waiting for first DataEvent (ExecuteMethod) and default lifetime/eta for status-driven commands.
DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS: float = 60.0

# Delay (seconds) after command start before starting GetStatus polling loop.
# Kept as module constant for backward compatibility with ODTCBackend.
POLLING_START_BUFFER: float = 10.0


class ODTCSiLAInterface(InhecoSiLAInterface):
  """ODTC-specific SiLA interface with parallelism, state tracking, and lockId validation.

  Extends InhecoSiLAInterface to support:
  - Multiple in-flight commands with parallelism enforcement per ODTC doc section 3
  - State machine tracking and command allowability per ODTC doc section 4
  - LockId validation (defaults to None, validates when device is locked)
  - Proper return code handling including device-specific codes (1000-2010)
  - All event types: ResponseEvent, StatusEvent, DataEvent, ErrorEvent
  """

  # Parallelism table from ODTC doc section 3
  # Format: {command: {other_command: "P" (parallel) or "S" (sequential)}}
  # Commands: SP=SetParameters, GP=GetParameters, OD=OpenDoor, CD=CloseDoor,
  # RAT=ReadActualTemperature, EM=ExecuteMethod, SM=StopMethod, GLD=GetLastData
  PARALLELISM_TABLE: Dict[str, Dict[str, str]] = {
    "SetParameters": {
      "SetParameters": "S",
      "GetParameters": "S",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "S",
      "StopMethod": "P",
      "GetLastData": "S",
    },
    "GetParameters": {
      "SetParameters": "P",
      "GetParameters": "S",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "S",
      "StopMethod": "P",
      "GetLastData": "S",
    },
    "OpenDoor": {
      "SetParameters": "P",
      "GetParameters": "P",
      "OpenDoor": "S",
      "CloseDoor": "S",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "P",
      "StopMethod": "P",
      "GetLastData": "P",
    },
    "CloseDoor": {
      "SetParameters": "P",
      "GetParameters": "P",
      "OpenDoor": "S",
      "CloseDoor": "S",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "P",
      "StopMethod": "P",
      "GetLastData": "P",
    },
    "ReadActualTemperature": {
      "SetParameters": "P",
      "GetParameters": "P",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "P",
      "StopMethod": "P",
      "GetLastData": "P",
    },
    "ExecuteMethod": {
      "SetParameters": "S",
      "GetParameters": "S",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "S",
      "StopMethod": "P",
      "GetLastData": "S",
    },
    "StopMethod": {
      "SetParameters": "P",
      "GetParameters": "P",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "S",
      "StopMethod": "S",
      "GetLastData": "P",
    },
    "GetLastData": {
      "SetParameters": "S",
      "GetParameters": "S",
      "OpenDoor": "P",
      "CloseDoor": "P",
      "ReadActualTemperature": "P",
      "ExecuteMethod": "S",
      "StopMethod": "P",
      "GetLastData": "S",
    },
  }

  # State allowability table from ODTC doc section 4
  STATE_ALLOWABILITY: Dict[str, Set[SiLAState]] = {
    "Abort": {SiLAState.IDLE, SiLAState.BUSY},
    "CloseDoor": {SiLAState.IDLE, SiLAState.BUSY},
    "DoContinue": {SiLAState.IDLE, SiLAState.BUSY},
    "ExecuteMethod": {SiLAState.IDLE, SiLAState.BUSY},
    "GetConfiguration": {SiLAState.STANDBY},
    "GetParameters": {SiLAState.IDLE, SiLAState.BUSY},
    "GetDeviceIdentification": {
      SiLAState.STARTUP,
      SiLAState.STANDBY,
      SiLAState.INITIALIZING,
      SiLAState.IDLE,
      SiLAState.BUSY,
    },
    "GetLastData": {SiLAState.IDLE, SiLAState.BUSY},
    "GetStatus": {
      SiLAState.STARTUP,
      SiLAState.STANDBY,
      SiLAState.INITIALIZING,
      SiLAState.IDLE,
      SiLAState.BUSY,
    },
    "Initialize": {SiLAState.STANDBY},
    "LockDevice": {SiLAState.STANDBY},
    "OpenDoor": {SiLAState.IDLE, SiLAState.BUSY},
    "Pause": {SiLAState.IDLE, SiLAState.BUSY},
    "PrepareForInput": {SiLAState.IDLE, SiLAState.BUSY},
    "PrepareForOutput": {SiLAState.IDLE, SiLAState.BUSY},
    "ReadActualTemperature": {SiLAState.IDLE, SiLAState.BUSY},
    "Reset": {SiLAState.STARTUP, SiLAState.STANDBY, SiLAState.IDLE, SiLAState.BUSY},
    "SetConfiguration": {SiLAState.STANDBY},
    "SetParameters": {SiLAState.IDLE, SiLAState.BUSY},
    "StopMethod": {SiLAState.IDLE, SiLAState.BUSY},
    "UnlockDevice": {SiLAState.STANDBY},
  }

  # Synchronous commands (return code 1, no ResponseEvent)
  SYNCHRONOUS_COMMANDS: Set[str] = {"GetStatus", "GetDeviceIdentification"}

  # Device-specific return codes that indicate DeviceError (InError state)
  DEVICE_ERROR_CODES: Set[int] = {1000, 2000, 2001, 2007}

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    lifetime_of_execution: Optional[float] = None,
  ) -> None:
    super().__init__(machine_ip=machine_ip, client_ip=client_ip, logger=logger)

    self._lifetime_of_execution = lifetime_of_execution
    self._lock_id: Optional[str] = None  # None = unlocked, str = locked with this ID

    # Track currently executing commands for parallelism checking
    self._executing_commands: Set[str] = set()

    # Lock ID tracking per pending request (for LockDevice)
    self._pending_lock_ids: Dict[int, str] = {}

    # DataEvent storage by request_id
    self._data_events_by_request_id: Dict[int, List[Dict[str, Any]]] = {}
    self.data_event_log_path: Optional[str] = None

  def _check_state_allowability(self, command: str) -> bool:
    """Check if command is allowed in current state.

    Args:
      command: Command name.

    Returns:
      True if command is allowed, False otherwise.
    """
    if command not in self.STATE_ALLOWABILITY:
      return True
    return self._current_state in self.STATE_ALLOWABILITY[command]

  def _check_parallelism(self, command: str) -> bool:
    """Check if command can run in parallel with currently executing commands.

    Args:
      command: Command name to check.

    Returns:
      True if command can run in parallel, False if it conflicts.
    """
    # If no commands executing, allow it
    if not self._executing_commands:
      return True

    # Normalize the new command name
    new_cmd = self._normalize_command_name(command)

    # Check against each executing command
    # The parallelism table is keyed by the EXECUTING command, then lists what can run in parallel
    for executing_cmd in self._executing_commands:
      # Normalize executing command name
      exec_cmd = self._normalize_command_name(executing_cmd)

      # Check parallelism table from the perspective of the EXECUTING command
      if exec_cmd in self.PARALLELISM_TABLE:
        if new_cmd in self.PARALLELISM_TABLE[exec_cmd]:
          if self.PARALLELISM_TABLE[exec_cmd][new_cmd] == "S":
            return False  # Sequential required
          # If "P" (parallel), continue checking other executing commands
        else:
          # New command not in executing command's table - default to sequential for safety
          return False
      else:
        # Executing command not in parallelism table - default to sequential
        return False

    # All checks passed - can run in parallel with all executing commands
    return True

  def _normalize_command_name(self, command: str) -> str:
    """Normalize command name for parallelism table lookup.

    Handles command aliases (OpenDoor/PrepareForOutput, CloseDoor/PrepareForInput).

    Args:
      command: Command name.

    Returns:
      Normalized command name for table lookup.
    """
    # Handle aliases per ODTC doc section 8
    if command in ("PrepareForOutput", "OpenDoor"):
      return "OpenDoor"
    if command in ("PrepareForInput", "CloseDoor"):
      return "CloseDoor"
    return command

  async def wait_for_first_data_event(
    self,
    request_id: int,
    timeout_seconds: float,
  ) -> Optional[Dict[str, Any]]:
    """Wait for the first DataEvent for this request_id, or raise on timeout."""
    started_at = time.time()
    while True:
      events = self._data_events_by_request_id.get(request_id) or []
      if events:
        return events[0]
      if time.time() - started_at >= timeout_seconds:
        raise FirstEventTimeout(
          f"No DataEvent received for request_id {request_id} within {timeout_seconds}s"
        )
      await asyncio.sleep(0.2)

  def _complete_pending(
    self,
    request_id: int,
    result: Any = None,
    exception: Optional[BaseException] = None,
  ) -> None:
    """ODTC cleanup (lock state, executing commands, BUSY->IDLE) then delegate to base."""
    pending = self._pending_by_id.get(request_id)
    if pending is None or pending.fut.done():
      return

    # Update lock state only on success
    if exception is None:
      lock_id = self._pending_lock_ids.pop(request_id, None)
      if pending.name == "LockDevice" and lock_id is not None:
        self._lock_id = lock_id
        self._logger.info(f"Device locked with lockId: {self._lock_id}")
      elif pending.name == "UnlockDevice":
        self._lock_id = None
        self._logger.info("Device unlocked")
      elif pending.name == "Reset":
        self._lock_id = None
        self._logger.info("Device reset (unlocked)")
    else:
      self._pending_lock_ids.pop(request_id, None)

    normalized_cmd = self._normalize_command_name(pending.name)
    self._executing_commands.discard(normalized_cmd)

    if not self._executing_commands and self._current_state == SiLAState.BUSY:
      self._current_state = SiLAState.IDLE

    super()._complete_pending(request_id, result=result, exception=exception)

  def _handle_return_code(
    self, return_code: int, message: str, command_name: str, request_id: int
  ) -> None:
    """Override to include current state in code-9 error message."""
    if return_code == 9:
      raise SiLAError(
        f"Command {command_name} not allowed in state {self._current_state.value} (return code 9)",
        code=9,
      )
    super()._handle_return_code(return_code, message, command_name, request_id)

  def _handle_device_return_code(self, return_code: int, message: str, command_name: str) -> None:
    """Handle ODTC device-specific return codes (1000+)."""
    if return_code in self.DEVICE_ERROR_CODES:
      self._current_state = SiLAState.INERROR
      raise SiLAError(
        f"Command {command_name} failed with device error (return code {return_code}): {message}",
        code=return_code,
      )
    # Warning or recoverable error
    self._logger.warning(
      f"Command {command_name} returned device-specific code {return_code}: {message}"
    )
    if return_code not in {2005, 2006, 2008, 2009, 2010}:
      self._current_state = SiLAState.ERRORHANDLING

  def _on_data_event(self, data_event: dict) -> None:
    request_id = data_event.get("requestId")
    if request_id is None:
      return

    if request_id not in self._data_events_by_request_id:
      self._data_events_by_request_id[request_id] = []
    self._data_events_by_request_id[request_id].append(data_event)

    progress = ODTCProgress.from_data_event(data_event, None)
    self._logger.debug(
      "DataEvent requestId %s: elapsed %.0fs, block %.1f°C, target %.1f°C, lid %.1f°C",
      request_id,
      progress.elapsed_s,
      progress.current_temp_c or 0.0,
      progress.target_temp_c or 0.0,
      progress.lid_temp_c or 0.0,
    )

    if self.data_event_log_path:
      try:
        with open(self.data_event_log_path, "a", encoding="utf-8") as f:
          f.write(json.dumps(data_event, default=str) + "\n")
      except OSError as e:
        self._logger.warning("Failed to append DataEvent to %s: %s", self.data_event_log_path, e)

  async def send_command(self, command: str, **kwargs: Any) -> Any:
    if command in self.SYNCHRONOUS_COMMANDS:
      if not self._check_state_allowability(command):
        raise SiLAError(
          f"Command {command} not allowed in state {self._current_state.value} (return code 9)",
          code=9,
        )
      return await super().send_command(command, **kwargs)
    fut, _, _ = await self.start_command(command, **kwargs)
    return await fut

  async def start_command(
    self, command: str, **kwargs: Any
  ) -> Tuple[asyncio.Future[Any], int, float]:
    if not self._check_state_allowability(command):
      raise SiLAError(
        f"Command {command} not allowed in state {self._current_state.value} (return code 9)",
        code=9,
      )

    normalized = self._normalize_command_name(command)
    if normalized in self.PARALLELISM_TABLE:
      if not self._check_parallelism(normalized):
        raise SiLAError(
          f"Command {command} cannot run in parallel with currently executing commands (return code 4)",
          code=4,
        )
    elif self._executing_commands:
      raise SiLAError(
        f"Command {command} not in parallelism table and device is busy (return code 4)",
        code=4,
      )

    # Auto-inject lockId when device is locked
    if self._lock_id is not None and "lockId" not in kwargs:
      kwargs["lockId"] = self._lock_id

    fut, request_id, started_at = await super().start_command(command, **kwargs)

    self._executing_commands.add(normalized)
    if self._current_state == SiLAState.IDLE:
      self._current_state = SiLAState.BUSY
    if command == "LockDevice" and "lockId" in kwargs:
      self._pending_lock_ids[request_id] = kwargs["lockId"]

    return fut, request_id, started_at
