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
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set

import xml.etree.ElementTree as ET

from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface
from pylabrobot.storage.inheco.scila.soap import soap_decode, soap_encode, XSI

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


# -----------------------------------------------------------------------------
# SOAP responses for events
# -----------------------------------------------------------------------------

SOAP_RESPONSE_ResponseEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <ResponseEventResponse xmlns="http://sila.coop">
      <ResponseEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0006262S</duration>
        <deviceClass>0</deviceClass>
      </ResponseEventResult>
    </ResponseEventResponse>
  </s:Body>
</s:Envelope>"""

SOAP_RESPONSE_StatusEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <StatusEventResponse xmlns="http://sila.coop">
      <StatusEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0005967S</duration>
        <deviceClass>0</deviceClass>
      </StatusEventResult>
    </StatusEventResponse>
  </s:Body>
</s:Envelope>"""

SOAP_RESPONSE_DataEventResponse = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <DataEventResponse xmlns="http://sila.coop">
      <DataEventResult>
        <returnCode>1</returnCode>
        <message>Success</message>
        <duration>PT0.0005967S</duration>
        <deviceClass>0</deviceClass>
      </DataEventResult>
    </DataEventResponse>
  </s:Body>
</s:Envelope>"""

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


class SiLAState(str, Enum):
  """SiLA device states per specification.

  Note: State values match what the ODTC device actually returns.
  Based on SCILABackend comment, devices return: "standby", "inError", "startup" (mixed/lowercase).
  However, the actual ODTC device returns "standby" (all lowercase) as seen in practice.
  """

  STARTUP = "startup"
  STANDBY = "standby"  # Device returns "standby" (all lowercase) or "standBy" (camelCase)
  INITIALIZING = "initializing"
  IDLE = "idle"
  BUSY = "busy"
  PAUSED = "paused"
  ERRORHANDLING = "errorHandling"  # Device returns "errorHandling" (camelCase per SCILABackend pattern)
  INERROR = "inError"  # Device returns "inError" (camelCase per SCILABackend comment)


class FirstEventType(str, Enum):
  """Event type to wait for before handing off an async command handle.

  Per SiLA Device Control & Data Interface Spec and ODTC TD_SILA_FWCommandSet:
  - DataEvent: transmission of data during async command execution (has requestId).
    Used by ExecuteMethod (methods and premethods) per ODTC firmware.
  - StatusEvent: unsolicited device state changes. Used by OpenDoor, CloseDoor,
    Initialize, Reset, LockDevice, UnlockDevice, StopMethod (no DataEvent stream).
  """

  DATA_EVENT = "DataEvent"
  STATUS_EVENT = "StatusEvent"


# Command -> event type to wait for (first event before returning handle).
# Verified against SiLA_Device_Control__Data__Interface_Specification_V1.2.01 and
# TD_SILA_FWCommandSet (ODTC): only ExecuteMethod sends DataEvents; others use StatusEvent.
COMMAND_FIRST_EVENT_TYPE: Dict[str, FirstEventType] = {
  "ExecuteMethod": FirstEventType.DATA_EVENT,
  "OpenDoor": FirstEventType.STATUS_EVENT,
  "CloseDoor": FirstEventType.STATUS_EVENT,
  "Initialize": FirstEventType.STATUS_EVENT,
  "Reset": FirstEventType.STATUS_EVENT,
  "LockDevice": FirstEventType.STATUS_EVENT,
  "UnlockDevice": FirstEventType.STATUS_EVENT,
  "StopMethod": FirstEventType.STATUS_EVENT,
}


# Default max wait for async command completion (3 hours). SiLA2-aligned: protocol execution always bounded.
DEFAULT_LIFETIME_OF_EXECUTION: float = 10800.0

# Default timeout for waiting for first DataEvent (ExecuteMethod) and default lifetime/eta for status-driven commands.
DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS: float = 60.0

# Delay (seconds) after command start before starting GetStatus polling loop.
POLLING_START_BUFFER: float = 10.0


@dataclass(frozen=True)
class PendingCommand:
  """Tracks a pending async command."""

  name: str
  request_id: int
  fut: asyncio.Future[Any]
  started_at: float
  lock_id: Optional[str] = None  # LockId sent with LockDevice command (for tracking)


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
  # Format: {command: {state: True if allowed}}
  STATE_ALLOWABILITY: Dict[str, Dict[SiLAState, bool]] = {
    "Abort": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "CloseDoor": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "DoContinue": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "ExecuteMethod": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetConfiguration": {SiLAState.STARTUP: False, SiLAState.STANDBY: True, SiLAState.IDLE: False, SiLAState.BUSY: False},
    "GetParameters": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetDeviceIdentification": {SiLAState.STARTUP: True, SiLAState.STANDBY: True, SiLAState.INITIALIZING: True, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetLastData": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetStatus": {SiLAState.STARTUP: True, SiLAState.STANDBY: True, SiLAState.INITIALIZING: True, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "Initialize": {SiLAState.STARTUP: False, SiLAState.STANDBY: True, SiLAState.IDLE: False, SiLAState.BUSY: False},
    "LockDevice": {SiLAState.STARTUP: False, SiLAState.STANDBY: True, SiLAState.IDLE: False, SiLAState.BUSY: False},
    "OpenDoor": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "Pause": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "PrepareForInput": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "PrepareForOutput": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "ReadActualTemperature": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "Reset": {SiLAState.STARTUP: True, SiLAState.STANDBY: True, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "SetConfiguration": {SiLAState.STARTUP: False, SiLAState.STANDBY: True, SiLAState.IDLE: False, SiLAState.BUSY: False},
    "SetParameters": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "StopMethod": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "UnlockDevice": {SiLAState.STARTUP: False, SiLAState.STANDBY: True, SiLAState.IDLE: False, SiLAState.BUSY: False},
  }

  # Synchronous commands (return code 1, no ResponseEvent)
  SYNCHRONOUS_COMMANDS: Set[str] = {"GetStatus", "GetDeviceIdentification"}

  # Device-specific return codes that indicate DeviceError (InError state)
  DEVICE_ERROR_CODES: Set[int] = {1000, 2000, 2001, 2007}

  # Terminal state for polling fallback: command name -> expected state when command is done (per STATE_ALLOWABILITY).
  ASYNC_COMMAND_TERMINAL_STATE: Dict[str, str] = {
    "Reset": "standby",
    "Initialize": "idle",
    "LockDevice": "standby",
    "UnlockDevice": "standby",
  }
  # Default terminal state for other async commands (OpenDoor, CloseDoor, ExecuteMethod, StopMethod, etc.)
  _DEFAULT_TERMINAL_STATE: str = "idle"

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    poll_interval: float = 5.0,
    lifetime_of_execution: Optional[float] = None,
    on_response_event_missing: Literal["warn_and_continue", "error"] = "warn_and_continue",
  ) -> None:
    """Initialize ODTC SiLA interface.

    Args:
      machine_ip: IP address of the ODTC device.
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
      poll_interval: Seconds between GetStatus calls in the polling fallback (SiLA2 subscribe_by_polling style).
      lifetime_of_execution: Max seconds to wait for async completion (SiLA2 deadline). If None, use 3 hours.
      on_response_event_missing: When polling sees terminal state but ResponseEvent was not received:
          "warn_and_continue" -> resolve with None and log warning; "error" -> set exception.
    """
    super().__init__(machine_ip=machine_ip, client_ip=client_ip, logger=logger)

    self._poll_interval = poll_interval
    self._lifetime_of_execution = lifetime_of_execution
    self._on_response_event_missing = on_response_event_missing

    # Multi-request tracking (replaces single _pending)
    self._pending_by_id: Dict[int, PendingCommand] = {}
    self._active_request_ids: Set[int] = set()  # For duplicate detection

    # State tracking
    self._current_state: SiLAState = SiLAState.STARTUP
    self._lock_id: Optional[str] = None  # None = unlocked, str = locked with this ID

    # Track currently executing commands for parallelism checking
    self._executing_commands: Set[str] = set()

    # Lock for parallelism checking (separate from base class's _making_request)
    self._parallelism_lock = asyncio.Lock()

    # DataEvent storage by request_id
    self._data_events_by_request_id: Dict[int, List[Dict[str, Any]]] = {}
    # Optional: when set, each received DataEvent payload is appended as one JSON line (for debugging / API discovery)
    self.data_event_log_path: Optional[str] = None
    # Estimated remaining time (s) per request_id; set by backend after first DataEvent so polling waits until eta+buffer.
    self._estimated_remaining_by_request_id: Dict[int, float] = {}

  def _check_state_allowability(self, command: str) -> bool:
    """Check if command is allowed in current state.

    Args:
      command: Command name.

    Returns:
      True if command is allowed, False otherwise.
    """
    if command not in self.STATE_ALLOWABILITY:
      # Unknown command - allow it (might be device-specific)
      return True

    state_rules = self.STATE_ALLOWABILITY[command]
    return state_rules.get(self._current_state, False)

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

  def _get_terminal_state(self, command: str) -> str:
    """Return the device state that indicates this async command has finished (for polling fallback)."""
    return self.ASYNC_COMMAND_TERMINAL_STATE.get(command, self._DEFAULT_TERMINAL_STATE)

  def get_first_event_type_for_command(self, command: str) -> FirstEventType:
    """Return which event type to wait for before handing off the handle (per SiLA/ODTC docs)."""
    normalized = self._normalize_command_name(command)
    return COMMAND_FIRST_EVENT_TYPE.get(normalized, FirstEventType.STATUS_EVENT)

  async def wait_for_first_event(
    self,
    request_id: int,
    event_type: FirstEventType,
    timeout_seconds: float,
  ) -> Optional[Dict[str, Any]]:
    """Wait for the first event of the given type for this request_id, or raise on timeout.

    For DATA_EVENT: polls _data_events_by_request_id until at least one event or timeout.
    For STATUS_EVENT: returns None immediately (StatusEvent has no requestId per SiLA spec).

    Args:
      request_id: SiLA request ID of the command.
      event_type: FirstEventType.DATA_EVENT or FirstEventType.STATUS_EVENT.
      timeout_seconds: Max seconds to wait (DATA_EVENT only).

    Returns:
      First event payload dict (DATA_EVENT) or None (STATUS_EVENT).

    Raises:
      FirstEventTimeout: If no DataEvent received within timeout_seconds.
    """
    if event_type == FirstEventType.STATUS_EVENT:
      return None
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
    update_lock_state: bool = True,
  ) -> None:
    """Complete a pending command: cleanup and resolve its Future (single place for ResponseEvent and polling).

    Args:
      request_id: Pending command request_id.
      result: Value to set on Future (ignored if exception is set).
      exception: If set, set_exception on Future instead of set_result(result).
      update_lock_state: If True (ResponseEvent path), apply LockDevice/UnlockDevice/Reset lock updates.
    """
    pending = self._pending_by_id.get(request_id)
    if pending is None or pending.fut.done():
      return

    if update_lock_state:
      if pending.name == "LockDevice" and pending.lock_id is not None:
        self._lock_id = pending.lock_id
        self._logger.info(f"Device locked with lockId: {self._lock_id}")
      elif pending.name == "UnlockDevice":
        self._lock_id = None
        self._logger.info("Device unlocked")
      elif pending.name == "Reset":
        self._lock_id = None
        self._logger.info("Device reset (unlocked)")

    self._pending_by_id.pop(request_id, None)
    self._estimated_remaining_by_request_id.pop(request_id, None)
    self._active_request_ids.discard(request_id)
    normalized_cmd = self._normalize_command_name(pending.name)
    self._executing_commands.discard(normalized_cmd)

    if not self._executing_commands and self._current_state == SiLAState.BUSY:
      self._current_state = SiLAState.IDLE

    if exception is not None:
      pending.fut.set_exception(exception)
    else:
      pending.fut.set_result(result)

  def set_estimated_remaining_time(self, request_id: int, eta_seconds: float) -> None:
    """Set device-estimated remaining time for a pending command so polling waits until eta+buffer.

    Call after receiving the first DataEvent (ExecuteMethod) or when eta is known.
    The _poll_until_complete task will not start GetStatus polling until
    time.time() >= started_at + eta_seconds + POLLING_START_BUFFER.

    Args:
      request_id: Pending command request_id.
      eta_seconds: Estimated remaining duration in seconds (0 = only wait POLLING_START_BUFFER).
    """
    self._estimated_remaining_by_request_id[request_id] = max(0.0, eta_seconds)

  def _validate_lock_id(self, lock_id: Optional[str]) -> None:
    """Validate lockId parameter.

    Args:
      lock_id: LockId to validate (None is allowed if device not locked).

    Raises:
      RuntimeError: If lockId validation fails (return code 5).
    """
    if self._lock_id is None:
      # Device not locked - any lockId (including None) is fine
      return

    # Device is locked - must provide matching lockId
    if lock_id != self._lock_id:
      raise SiLAError(
        f"Device is locked with lockId '{self._lock_id}', "
        f"but command provided lockId '{lock_id}'. Return code: 5",
        code=5,
      )

  def _update_state_from_status(self, state_str: str) -> None:
    """Update internal state from GetStatus or StatusEvent response.

    Args:
      state_str: State string from device response. Must match enum values exactly.
    """
    if not state_str:
      self._logger.debug("_update_state_from_status: Empty state string, skipping update")
      return

    self._logger.debug(f"_update_state_from_status: Received state: {state_str!r} (type: {type(state_str).__name__})")

    # Match exactly against enum values (no normalization - we want to see what device actually returns)
    try:
      self._current_state = SiLAState(state_str)
      self._logger.debug(f"State updated to: {self._current_state.value}")
    except ValueError:
      self._logger.warning(
        f"Unknown state received: {state_str!r}, keeping current state {self._current_state.value}. "
        f"Expected one of: {[s.value for s in SiLAState]}"
      )

  def _handle_return_code(
    self, return_code: int, message: str, command_name: str, request_id: int
  ) -> None:
    """Handle return code and update state accordingly.

    Args:
      return_code: Return code from device.
      message: Return message.
      command_name: Name of the command.
      request_id: Request ID of the command.

    Raises:
      RuntimeError: For error return codes.
    """
    if return_code == 1:
      # Success (synchronous commands)
      return
    elif return_code == 2:
      # Asynchronous command accepted
      return
    elif return_code == 3:
      # Asynchronous command finished (success) - handled in ResponseEvent
      return
    elif return_code == 4:
      raise SiLAError(f"Command {command_name} rejected: Device is busy (return code 4)", code=4)
    elif return_code == 5:
      raise SiLAError(f"Command {command_name} rejected: LockId mismatch (return code 5)", code=5)
    elif return_code == 6:
      raise SiLAError(f"Command {command_name} rejected: Invalid or duplicate requestId (return code 6)", code=6)
    elif return_code == 9:
      raise SiLAError(
        f"Command {command_name} not allowed in state {self._current_state.value} (return code 9)",
        code=9,
      )
    elif return_code == 11:
      raise SiLAError(f"Command {command_name} rejected: Invalid parameter (return code 11): {message}", code=11)
    elif return_code == 12:
      # Finished with warning
      self._logger.warning(f"Command {command_name} finished with warning (return code 12): {message}")
      return
    elif return_code >= 1000:
      # Device-specific return code
      if return_code in self.DEVICE_ERROR_CODES:
        self._current_state = SiLAState.INERROR
        raise SiLAError(
          f"Command {command_name} failed with device error (return code {return_code}): {message}",
          code=return_code,
        )
      else:
        # Warning or recoverable error
        self._logger.warning(
          f"Command {command_name} returned device-specific code {return_code}: {message}"
        )
        # May transition to ErrorHandling if recoverable
        if return_code not in {2005, 2006, 2008, 2009, 2010}:  # These are warnings, not errors
          # Recoverable error - transition to ErrorHandling
          self._current_state = SiLAState.ERRORHANDLING
    else:
      # Unknown return code
      raise SiLAError(f"Command {command_name} returned unknown code {return_code}: {message}")

  async def _on_http(self, req: InhecoSiLAInterface._HTTPRequest) -> bytes:
    """Handle incoming HTTP requests from device (events).

    Overrides base class to support multiple pending requests and all event types.

    Args:
      req: HTTP request from device.

    Returns:
      SOAP response bytes.
    """
    body_str = req.body.decode("utf-8")
    decoded = soap_decode(body_str)

    # Handle ResponseEvent (async command completion)
    if "ResponseEvent" in decoded:
      response_event = decoded["ResponseEvent"]
      request_id = response_event.get("requestId")
      return_value = response_event.get("returnValue", {})
      return_code = return_value.get("returnCode")
      message = return_value.get("message", "")

      if request_id is None:
        self._logger.warning("ResponseEvent missing requestId")
        return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

      pending = self._pending_by_id.get(request_id)
      if pending is None:
        self._logger.warning(f"ResponseEvent for unknown requestId: {request_id}")
        return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

      if pending.fut.done():
        self._logger.warning(f"ResponseEvent for already-completed requestId: {request_id}")
        return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

      # Code 3 = async finished (SUCCESS)
      if return_code == 3:
        response_data = response_event.get("responseData", "")
        if response_data and response_data.strip():
          try:
            root = ET.fromstring(response_data)
            self._complete_pending(request_id, result=root, update_lock_state=True)
          except ET.ParseError as e:
            self._logger.error(f"Failed to parse ResponseEvent responseData: {e}")
            self._complete_pending(
              request_id,
              exception=RuntimeError(f"Failed to parse response data: {e}"),
              update_lock_state=False,
            )
        else:
          self._complete_pending(request_id, result=None, update_lock_state=True)
      else:
        err_msg = message.replace("\n", " ") if message else f"Unknown error (code {return_code})"
        self._complete_pending(
          request_id,
          exception=RuntimeError(f"Command {pending.name} failed with code {return_code}: '{err_msg}'"),
          update_lock_state=False,
        )

      return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

    # Handle StatusEvent (state changes)
    if "StatusEvent" in decoded:
      status_event = decoded["StatusEvent"]
      event_description = status_event.get("eventDescription", {})
      device_state = event_description.get("DeviceState")
      if device_state:
        self._update_state_from_status(device_state)
      return SOAP_RESPONSE_StatusEventResponse.encode("utf-8")

    # Handle DataEvent (intermediate data, e.g., during ExecuteMethod)
    if "DataEvent" in decoded:
      data_event = decoded["DataEvent"]
      request_id = data_event.get("requestId")

      if request_id is not None:
        # Store the full DataEvent payload
        if request_id not in self._data_events_by_request_id:
          self._data_events_by_request_id[request_id] = []
        self._data_events_by_request_id[request_id].append(data_event)

        self._logger.debug(
          f"DataEvent received for requestId {request_id} "
          f"(total: {len(self._data_events_by_request_id[request_id])})"
        )
        # One-line summary at DEBUG so default "waiting" shows only backend progress
        # (every progress_log_interval, e.g. 150 s). Backend logs use correct target for premethods.
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

      return SOAP_RESPONSE_DataEventResponse.encode("utf-8")

    # Handle ErrorEvent (recoverable errors with continuation tasks)
    if "ErrorEvent" in decoded:
      error_event = decoded["ErrorEvent"]
      req_id = error_event.get("requestId")
      return_value = error_event.get("returnValue", {})
      return_code = return_value.get("returnCode")
      message = return_value.get("message", "")

      self._logger.error(f"ErrorEvent for requestId {req_id}: code {return_code}, message: {message}")

      self._current_state = SiLAState.ERRORHANDLING

      err_msg = message.replace("\n", " ") if message else f"Error (code {return_code})"
      pending_err = self._pending_by_id.get(req_id)
      if pending_err and not pending_err.fut.done():
        self._complete_pending(
          req_id,
          exception=RuntimeError(f"Command {pending_err.name} error: '{err_msg}'"),
          update_lock_state=False,
        )

      return SOAP_RESPONSE_ErrorEventResponse.encode("utf-8")

    # Unknown event type
    self._logger.warning("Unknown event type received")
    return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

  async def _execute_command(
    self,
    command: str,
    lock_id: Optional[str] = None,
    **kwargs: Any,
  ) -> Any | tuple[asyncio.Future[Any], int, float]:
    """Execute a SiLA command; return decoded dict (sync) or (fut, request_id, started_at) (async).

    Internal helper used by send_command and start_command. Callers should use
    send_command (run and return result) or start_command (start and return handle).
    """
    if self._closed:
      raise RuntimeError("Interface is closed")

    if command != "GetStatus":
      self._validate_lock_id(lock_id)

    if not self._check_state_allowability(command):
      raise SiLAError(
        f"Command {command} not allowed in state {self._current_state.value} (return code 9)",
        code=9,
      )

    if command not in self.SYNCHRONOUS_COMMANDS:
      normalized_cmd = self._normalize_command_name(command)
      if normalized_cmd in self.PARALLELISM_TABLE:
        async with self._parallelism_lock:
          if not self._check_parallelism(normalized_cmd):
            raise SiLAError(
              f"Command {command} cannot run in parallel with currently executing commands (return code 4)",
              code=4,
            )
      else:
        async with self._parallelism_lock:
          if self._executing_commands:
            raise SiLAError(
              f"Command {command} not in parallelism table and device is busy (return code 4)",
              code=4,
            )
    else:
      normalized_cmd = self._normalize_command_name(command)

    request_id = self._make_request_id()
    if request_id in self._active_request_ids:
      raise SiLAError(f"Duplicate requestId generated: {request_id} (return code 6)", code=6)

    params: Dict[str, Any] = {"requestId": request_id, **kwargs}
    if command != "GetStatus":
      if self._lock_id is not None:
        params["lockId"] = lock_id if lock_id is not None else self._lock_id
      elif lock_id is not None:
        params["lockId"] = lock_id

    cmd_xml = soap_encode(
      command,
      params,
      method_ns="http://sila.coop",
      extra_method_xmlns={"i": XSI},
    )

    url = f"http://{self._machine_ip}:8080/"
    req = urllib.request.Request(
      url=url,
      data=cmd_xml.encode("utf-8"),
      method="POST",
      headers={
        "Content-Type": "text/xml; charset=utf-8",
        "Content-Length": str(len(cmd_xml)),
        "SOAPAction": f"http://sila.coop/{command}",
        "Expect": "100-continue",
        "Accept-Encoding": "gzip, deflate",
      },
    )

    def _do_request() -> bytes:
      with urllib.request.urlopen(req) as resp:
        return resp.read()  # type: ignore

    body = await asyncio.to_thread(_do_request)
    decoded = soap_decode(body.decode("utf-8"))
    return_code, message = self._get_return_code_and_message(command, decoded)
    self._logger.debug(f"Command {command} returned code {return_code}: {message}")

    if return_code == 1:
      if command == "GetStatus":
        get_status_response = decoded.get("GetStatusResponse", {})
        state = get_status_response.get("state")
        if state:
          self._update_state_from_status(state)
      return decoded

    if return_code == 2:
      fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

      pending_lock_id = None
      if command == "LockDevice" and "lockId" in params:
        pending_lock_id = params["lockId"]

      started_at = time.time()
      pending = PendingCommand(
        name=command,
        request_id=request_id,
        fut=fut,
        started_at=started_at,
        lock_id=pending_lock_id,
      )

      self._pending_by_id[request_id] = pending
      self._active_request_ids.add(request_id)
      self._executing_commands.add(normalized_cmd)

      if self._current_state == SiLAState.IDLE:
        self._current_state = SiLAState.BUSY

      effective_lifetime = (
        self._lifetime_of_execution
        if self._lifetime_of_execution is not None
        else DEFAULT_LIFETIME_OF_EXECUTION
      )

      async def _poll_until_complete() -> None:
        while True:
          pending_ref = self._pending_by_id.get(request_id)
          if pending_ref is None or pending_ref.fut.done():
            break
          eta = self._estimated_remaining_by_request_id.get(request_id) or 0.0
          remaining_wait = (
            started_at + eta + POLLING_START_BUFFER - time.time()
          )
          if remaining_wait > 0:
            await asyncio.sleep(min(remaining_wait, self._poll_interval))
            continue
          break
        while True:
          pending_ref = self._pending_by_id.get(request_id)
          if pending_ref is None:
            break
          if pending_ref.fut.done():
            break
          elapsed = time.time() - pending_ref.started_at
          if elapsed >= effective_lifetime:
            self._complete_pending(
              request_id,
              exception=SiLATimeoutError(
                f"Command {pending_ref.name} timed out (lifetime_of_execution exceeded: {effective_lifetime}s)"
              ),
              update_lock_state=False,
            )
            break
          try:
            decoded_status = await self.send_command("GetStatus")
          except Exception:
            await asyncio.sleep(self._poll_interval)
            continue
          state = decoded_status.get("GetStatusResponse", {}).get("state")
          if state:
            self._update_state_from_status(state)
          terminal_state = self._get_terminal_state(pending_ref.name)
          if state == terminal_state:
            if self._on_response_event_missing == "warn_and_continue":
              self._logger.warning(
                "ResponseEvent not received; completed via GetStatus polling (possible sleep/network loss)"
              )
              self._complete_pending(request_id, result=None, update_lock_state=False)
            else:
              self._complete_pending(
                request_id,
                exception=SiLATimeoutError(
                  "ResponseEvent not received; device reported idle. Possible callback loss (e.g. sleep/network)."
                ),
                update_lock_state=False,
              )
            break
          await asyncio.sleep(self._poll_interval)

      asyncio.create_task(_poll_until_complete())
      return (fut, request_id, started_at)

    self._handle_return_code(return_code, message, command, request_id)
    raise SiLAError(f"Command {command} failed: {return_code} {message}")

  async def send_command(
    self,
    command: str,
    lock_id: Optional[str] = None,
    **kwargs: Any,
  ) -> Any:
    """Run a SiLA command and return the result (blocking until done).

    Use for any command when you want the decoded result. For async device
    commands (e.g. OpenDoor, ExecuteMethod), this awaits completion and
    returns the result (or raises). For sync commands (GetStatus,
    GetDeviceIdentification), returns the decoded dict immediately.

    Args:
      command: Command name.
      lock_id: LockId (defaults to None, validated if device is locked).
      **kwargs: Additional command parameters.

    Returns:
      Decoded response dict (sync) or result after awaiting (async).

    Raises:
      SiLAError and subclasses: For validation, return code, or state violations.
    """
    result = await self._execute_command(command, lock_id=lock_id, **kwargs)
    if isinstance(result, tuple):
      return await result[0]
    return result

  async def start_command(
    self,
    command: str,
    lock_id: Optional[str] = None,
    **kwargs: Any,
  ) -> tuple[asyncio.Future[Any], int, float]:
    """Start a SiLA command and return a handle (future, request_id, started_at).

    Use for async device commands (OpenDoor, CloseDoor, ExecuteMethod,
    Initialize, Reset, LockDevice, UnlockDevice, StopMethod) when you want
    a handle to await, poll, or compose with. Do not use for sync-only
    commands (GetStatus, GetDeviceIdentification); use send_command instead.

    Args:
      command: Command name (must be an async command).
      lock_id: LockId (defaults to None, validated if device is locked).
      **kwargs: Additional command parameters (e.g. methodName, deviceId). Not sent to
        device: requestId (injected), lockId when applicable.

    Returns:
      (future, request_id, started_at) tuple. ETA/lifetime come from the backend (event-driven).
      Await the future for the result or to propagate exceptions.

    Raises:
      ValueError: If the device returned sync (return_code 1); start_command
        is for async commands only.
      SiLAError and subclasses: For validation, return code, or state violations.
    """
    result = await self._execute_command(command, lock_id=lock_id, **kwargs)
    if isinstance(result, tuple):
      return result
    raise ValueError(
      "start_command is for async commands only; device returned sync response (return_code 1)"
    )
