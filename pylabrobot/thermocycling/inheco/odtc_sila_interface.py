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
import logging
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set

import xml.etree.ElementTree as ET

from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface
from pylabrobot.storage.inheco.scila.soap import soap_decode, soap_encode, XSI


# -----------------------------------------------------------------------------
# SiLA/ODTC exceptions (typed command and device errors)
# -----------------------------------------------------------------------------


class SiLAError(RuntimeError):
  """Base exception for SiLA command and device errors."""

  pass


class SiLACommandRejected(SiLAError):
  """Command rejected: device busy (return code 4) or not allowed in state (return code 9)."""

  pass


class SiLALockIdError(SiLAError):
  """LockId mismatch (return code 5)."""

  pass


class SiLARequestIdError(SiLAError):
  """Invalid or duplicate requestId (return code 6)."""

  pass


class SiLAParameterError(SiLAError):
  """Invalid command parameter (return code 11)."""

  pass


class SiLADeviceError(SiLAError):
  """Device-specific error (return codes 1000, 2000, 2001, 2007, etc.)."""

  pass


class SiLATimeoutError(SiLAError):
  """Command timed out: lifetime_of_execution exceeded or ResponseEvent not received."""

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


# Default max wait for async command completion (3 hours). SiLA2-aligned: protocol execution always bounded.
DEFAULT_LIFETIME_OF_EXECUTION: float = 10800.0

# Buffer (seconds) added to estimated_remaining_time before starting polling loop.
POLLING_START_BUFFER: float = 10.0


@dataclass(frozen=True)
class PendingCommand:
  """Tracks a pending async command."""

  name: str
  request_id: int
  fut: asyncio.Future[Any]
  started_at: float
  estimated_remaining_time: Optional[float] = None  # Caller-provided estimate (seconds)
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
    self._active_request_ids.discard(request_id)
    normalized_cmd = self._normalize_command_name(pending.name)
    self._executing_commands.discard(normalized_cmd)

    if not self._executing_commands and self._current_state == SiLAState.BUSY:
      self._current_state = SiLAState.IDLE

    if exception is not None:
      pending.fut.set_exception(exception)
    else:
      pending.fut.set_result(result)

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
      raise SiLALockIdError(
        f"Device is locked with lockId '{self._lock_id}', "
        f"but command provided lockId '{lock_id}'. Return code: 5"
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
      # Device busy
      raise SiLACommandRejected(f"Command {command_name} rejected: Device is busy (return code 4)")
    elif return_code == 5:
      # LockId error
      raise SiLALockIdError(f"Command {command_name} rejected: LockId mismatch (return code 5)")
    elif return_code == 6:
      # RequestId error
      raise SiLARequestIdError(f"Command {command_name} rejected: Invalid or duplicate requestId (return code 6)")
    elif return_code == 9:
      # Command not allowed in this state
      raise SiLACommandRejected(
        f"Command {command_name} not allowed in state {self._current_state.value} (return code 9)"
      )
    elif return_code == 11:
      # Invalid parameter
      raise SiLAParameterError(f"Command {command_name} rejected: Invalid parameter (return code 11): {message}")
    elif return_code == 12:
      # Finished with warning
      self._logger.warning(f"Command {command_name} finished with warning (return code 12): {message}")
      return
    elif return_code >= 1000:
      # Device-specific return code
      if return_code in self.DEVICE_ERROR_CODES:
        # DeviceError - transition to InError
        self._current_state = SiLAState.INERROR
        raise SiLADeviceError(
          f"Command {command_name} failed with device error (return code {return_code}): {message}"
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
  ) -> Any | tuple[asyncio.Future[Any], int, Optional[float], float]:
    """Execute a SiLA command; return decoded dict (sync) or (fut, request_id, eta, started_at) (async).

    Internal helper used by send_command and start_command. Callers should use
    send_command (run and return result) or start_command (start and return handle).
    """
    if self._closed:
      raise RuntimeError("Interface is closed")

    # Caller-provided estimate; must not be sent to device.
    estimated_duration_seconds: Optional[float] = kwargs.pop("estimated_duration_seconds", None)

    if command != "GetStatus":
      self._validate_lock_id(lock_id)

    if not self._check_state_allowability(command):
      raise SiLACommandRejected(
        f"Command {command} not allowed in state {self._current_state.value} (return code 9)"
      )

    if command not in self.SYNCHRONOUS_COMMANDS:
      normalized_cmd = self._normalize_command_name(command)
      if normalized_cmd in self.PARALLELISM_TABLE:
        async with self._parallelism_lock:
          if not self._check_parallelism(normalized_cmd):
            raise SiLACommandRejected(
              f"Command {command} cannot run in parallel with currently executing commands (return code 4)"
            )
      else:
        async with self._parallelism_lock:
          if self._executing_commands:
            raise SiLACommandRejected(
              f"Command {command} not in parallelism table and device is busy (return code 4)"
            )
    else:
      normalized_cmd = self._normalize_command_name(command)

    request_id = self._make_request_id()
    if request_id in self._active_request_ids:
      raise SiLARequestIdError(f"Duplicate requestId generated: {request_id} (return code 6)")

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
      estimated_remaining_time: Optional[float] = estimated_duration_seconds

      pending_lock_id = None
      if command == "LockDevice" and "lockId" in params:
        pending_lock_id = params["lockId"]

      started_at = time.time()
      pending = PendingCommand(
        name=command,
        request_id=request_id,
        fut=fut,
        started_at=started_at,
        estimated_remaining_time=estimated_remaining_time,
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
          remaining_wait = (
            started_at + (estimated_remaining_time or 0) + POLLING_START_BUFFER - time.time()
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
      return (fut, request_id, estimated_remaining_time, started_at)

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
  ) -> tuple[asyncio.Future[Any], int, Optional[float], float]:
    """Start a SiLA command and return a handle (future + request_id, eta, started_at).

    Use for async device commands (OpenDoor, CloseDoor, ExecuteMethod,
    Initialize, Reset, LockDevice, UnlockDevice, StopMethod) when you want
    a handle to await, poll, or compose with. Do not use for sync-only
    commands (GetStatus, GetDeviceIdentification); use send_command instead.

    Args:
      command: Command name (must be an async command).
      lock_id: LockId (defaults to None, validated if device is locked).
      **kwargs: Additional command parameters. May include estimated_duration_seconds
        (optional float, seconds); it is used as estimated_remaining_time on the handle
        and is not sent to the device.

    Returns:
      (future, request_id, estimated_remaining_time, started_at) tuple.
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
