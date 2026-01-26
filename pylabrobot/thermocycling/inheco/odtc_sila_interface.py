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
from typing import Any, Dict, List, Optional, Set

import xml.etree.ElementTree as ET

from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface
from pylabrobot.storage.inheco.scila.soap import soap_decode, soap_encode, XSI

# SOAP responses for events
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
  Based on SCILABackend comment, devices return: "standBy", "inError", "startup" (mixed/lowercase).
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


@dataclass(frozen=True)
class PendingCommand:
  """Tracks a pending async command."""

  name: str
  request_id: int
  fut: asyncio.Future[Any]
  started_at: float
  timeout: Optional[float] = None  # Estimated duration from device response
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
    "GetDeviceIdentification": {SiLAState.STARTUP: True, SiLAState.STANDBY: True, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetLastData": {SiLAState.STARTUP: False, SiLAState.STANDBY: False, SiLAState.IDLE: True, SiLAState.BUSY: True},
    "GetStatus": {SiLAState.STARTUP: True, SiLAState.STANDBY: True, SiLAState.IDLE: True, SiLAState.BUSY: True},
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

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    """Initialize ODTC SiLA interface.

    Args:
      machine_ip: IP address of the ODTC device.
      client_ip: IP address of this client (auto-detected if None).
      logger: Logger instance (creates one if None).
    """
    super().__init__(machine_ip=machine_ip, client_ip=client_ip, logger=logger)

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

    # Check against each executing command
    for executing_cmd in self._executing_commands:
      # Normalize command names (handle aliases)
      cmd1 = self._normalize_command_name(command)
      cmd2 = self._normalize_command_name(executing_cmd)

      # Check parallelism table
      if cmd1 in self.PARALLELISM_TABLE:
        if cmd2 in self.PARALLELISM_TABLE[cmd1]:
          if self.PARALLELISM_TABLE[cmd1][cmd2] == "S":
            return False  # Sequential required
        else:
          # Command not in table - default to sequential for safety
          return False
      else:
        # Command not in parallelism table - default to sequential
        return False

    return True  # Can run in parallel

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
      raise RuntimeError(
        f"Device is locked with lockId '{self._lock_id}', "
        f"but command provided lockId '{lock_id}'. Return code: 5"
      )

  def _update_state_from_status(self, state_str: str) -> None:
    """Update internal state from GetStatus or StatusEvent response.

    Args:
      state_str: State string from device response (must match enum values exactly).
    """
    if not state_str:
      return
    
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
      raise RuntimeError(f"Command {command_name} rejected: Device is busy (return code 4)")
    elif return_code == 5:
      # LockId error
      raise RuntimeError(f"Command {command_name} rejected: LockId mismatch (return code 5)")
    elif return_code == 6:
      # RequestId error
      raise RuntimeError(f"Command {command_name} rejected: Invalid or duplicate requestId (return code 6)")
    elif return_code == 9:
      # Command not allowed in this state
      raise RuntimeError(
        f"Command {command_name} not allowed in state {self._current_state.value} (return code 9)"
      )
    elif return_code == 11:
      # Invalid parameter
      raise RuntimeError(f"Command {command_name} rejected: Invalid parameter (return code 11): {message}")
    elif return_code == 12:
      # Finished with warning
      self._logger.warning(f"Command {command_name} finished with warning (return code 12): {message}")
      return
    elif return_code >= 1000:
      # Device-specific return code
      if return_code in self.DEVICE_ERROR_CODES:
        # DeviceError - transition to InError
        self._current_state = SiLAState.INERROR
        raise RuntimeError(
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
      raise RuntimeError(f"Command {command_name} returned unknown code {return_code}: {message}")

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

      # Find matching pending command
      pending = self._pending_by_id.get(request_id)
      if pending is None:
        self._logger.warning(f"ResponseEvent for unknown requestId: {request_id}")
        return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

      if pending.fut.done():
        self._logger.warning(f"ResponseEvent for already-completed requestId: {request_id}")
        return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

      # Fix: Code 3 means "async finished" (SUCCESS), not error
      if return_code == 3:
        # Success - extract response data
        response_data = response_event.get("responseData", "")
        if response_data and response_data.strip():
          try:
            root = ET.fromstring(response_data)
            pending.fut.set_result(root)
          except ET.ParseError as e:
            self._logger.error(f"Failed to parse ResponseEvent responseData: {e}")
            pending.fut.set_exception(RuntimeError(f"Failed to parse response data: {e}"))
        else:
          # No response data - still success (e.g., OpenDoor, CloseDoor)
          pending.fut.set_result(None)

        # Handle LockDevice/UnlockDevice/Reset to update lock state (only on success)
        if pending.name == "LockDevice" and pending.lock_id is not None:
          self._lock_id = pending.lock_id
          self._logger.info(f"Device locked with lockId: {self._lock_id}")
        elif pending.name == "UnlockDevice":
          self._lock_id = None
          self._logger.info("Device unlocked")
        elif pending.name == "Reset":
          # Reset unlocks device implicitly
          self._lock_id = None
          self._logger.info("Device reset (unlocked)")
      else:
        # Error or other code
        err_msg = message.replace("\n", " ") if message else f"Unknown error (code {return_code})"
        pending.fut.set_exception(RuntimeError(f"Command {pending.name} failed with code {return_code}: '{err_msg}'"))

      # Clean up
      self._pending_by_id.pop(request_id, None)
      self._active_request_ids.discard(request_id)
      # Use normalized command name for cleanup
      normalized_cmd = self._normalize_command_name(pending.name)
      self._executing_commands.discard(normalized_cmd)

      # Update state: if no more commands executing, transition Busy -> Idle
      if not self._executing_commands and self._current_state == SiLAState.BUSY:
        self._current_state = SiLAState.IDLE

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
      request_id = error_event.get("requestId")
      return_value = error_event.get("returnValue", {})
      return_code = return_value.get("returnCode")
      message = return_value.get("message", "")

      self._logger.error(f"ErrorEvent for requestId {request_id}: code {return_code}, message: {message}")

      # Transition to ErrorHandling state
      self._current_state = SiLAState.ERRORHANDLING

      # Find matching pending command and set exception
      pending = self._pending_by_id.get(request_id)
      if pending and not pending.fut.done():
        err_msg = message.replace("\n", " ") if message else f"Error (code {return_code})"
        pending.fut.set_exception(RuntimeError(f"Command {pending.name} error: '{err_msg}'"))

      # TODO: Continuation task selection/response not implemented (out of scope)
      return SOAP_RESPONSE_ErrorEventResponse.encode("utf-8")

    # Unknown event type
    self._logger.warning("Unknown event type received")
    return SOAP_RESPONSE_ResponseEventResponse.encode("utf-8")

  async def send_command(
    self,
    command: str,
    lock_id: Optional[str] = None,
    return_request_id: bool = False,
    **kwargs,
  ) -> Any | tuple[asyncio.Future[Any], int]:
    """Send a SiLA command with parallelism, state, and lockId validation.

    Overrides base class to add:
    - Parallelism checking
    - State allowability checking
    - LockId validation
    - Multi-request tracking
    - Proper return code handling

    Args:
      command: Command name.
      lock_id: LockId (defaults to None, validated if device is locked).
      return_request_id: If True and command is async (return_code==2),
          return (Future, request_id) tuple instead of awaiting Future.
          Caller must await the Future themselves.
      **kwargs: Additional command parameters.

    Returns:
      - For sync commands (return_code==1): decoded response dict
      - For async commands with return_request_id=False: result after awaiting Future
      - For async commands with return_request_id=True: (Future, request_id) tuple

    Raises:
      RuntimeError: For validation failures, return code errors, or state violations.
    """
    if self._closed:
      raise RuntimeError("Interface is closed")

    # GetStatus doesn't require lockId per ODTC doc section 2
    if command != "GetStatus":
      self._validate_lock_id(lock_id)

    # Check state allowability
    if not self._check_state_allowability(command):
      raise RuntimeError(
        f"Command {command} not allowed in state {self._current_state.value} (return code 9)"
      )

    # Check parallelism (for commands in the table)
    normalized_cmd = self._normalize_command_name(command)
    if normalized_cmd in self.PARALLELISM_TABLE:
      async with self._parallelism_lock:
        if not self._check_parallelism(normalized_cmd):
          raise RuntimeError(
            f"Command {command} cannot run in parallel with currently executing commands (return code 4)"
          )
    else:
      # Command not in parallelism table - default to sequential (safe)
      async with self._parallelism_lock:
        if self._executing_commands:
          # If any command is executing and this command isn't in table, reject
          raise RuntimeError(
            f"Command {command} not in parallelism table and device is busy (return code 4)"
          )

    # Generate request_id (reuse base class method)
    request_id = self._make_request_id()

    # Check for duplicate request_id (unlikely but guard against it)
    if request_id in self._active_request_ids:
      raise RuntimeError(f"Duplicate requestId generated: {request_id} (return code 6)")

    # Build command parameters
    params: Dict[str, Any] = {"requestId": request_id, **kwargs}
    # Add lockId if provided (or if device is locked, it's required)
    if command != "GetStatus":  # GetStatus exception
      if self._lock_id is not None:
        # Device is locked - must provide lockId
        params["lockId"] = lock_id if lock_id is not None else self._lock_id
      elif lock_id is not None:
        # Device not locked but lockId provided - include it
        params["lockId"] = lock_id

    # Encode SOAP request
    cmd_xml = soap_encode(
      command,
      params,
      method_ns="http://sila.coop",
      extra_method_xmlns={"i": XSI},
    )

    # Make POST request
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

    # Execute request
    def _do_request() -> bytes:
      with urllib.request.urlopen(req) as resp:
        return resp.read()  # type: ignore

    body = await asyncio.to_thread(_do_request)
    decoded = soap_decode(body.decode("utf-8"))

    # Extract return code and message
    return_code, message = self._get_return_code_and_message(command, decoded)

    # Handle return codes
    if return_code == 1:
      # Synchronous success (GetStatus, GetDeviceIdentification)
      # Update state from GetStatus response if applicable
      if command == "GetStatus":
        # Try different possible response structures
        state = decoded.get("GetStatusResponse", {}).get("state")
        if not state:
          state = decoded.get("GetStatusResponse", {}).get("GetStatusResult", {}).get("state")
        if state:
          self._update_state_from_status(state)
      return decoded

    elif return_code == 2:
      # Asynchronous command accepted - set up pending tracking
      fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

      # Extract duration for timeout (if provided)
      result = decoded.get(f"{command}Response", {}).get(f"{command}Result", {})
      duration_str = result.get("duration")
      timeout = None
      if duration_str:
        # Parse ISO 8601 duration (simplified - just extract seconds)
        # Format: PT30.7S or P5DT4H12M17S
        try:
          # For now, just use a multiplier - proper parsing would use datetime.timedelta
          # This is a simplified approach
          if isinstance(duration_str, str) and "S" in duration_str:
            # Extract seconds part
            import re
            match = re.search(r"(\d+(?:\.\d+)?)S", duration_str)
            if match:
              seconds = float(match.group(1))
              timeout = seconds + 10.0  # Add 10s buffer
        except Exception:
          pass  # Ignore parsing errors, use None timeout

      # Store lock_id for LockDevice commands so we can set it after success
      pending_lock_id = None
      if command == "LockDevice" and "lockId" in params:
        pending_lock_id = params["lockId"]

      pending = PendingCommand(
        name=command,
        request_id=request_id,
        fut=fut,
        started_at=time.time(),
        timeout=timeout,
        lock_id=pending_lock_id,
      )

      self._pending_by_id[request_id] = pending
      self._active_request_ids.add(request_id)
      self._executing_commands.add(normalized_cmd)

      # Update state: Idle -> Busy
      if self._current_state == SiLAState.IDLE:
        self._current_state = SiLAState.BUSY

      # Handle return_request_id parameter
      if return_request_id:
        # Return Future and request_id immediately (caller awaits)
        return (fut, request_id)
      else:
        # Existing behavior: await Future
        try:
          result = await fut
          return result
        except asyncio.TimeoutError:
          # Clean up on timeout
          self._pending_by_id.pop(request_id, None)
          self._active_request_ids.discard(request_id)
          self._executing_commands.discard(normalized_cmd)
          raise RuntimeError(f"Command {command} timed out waiting for ResponseEvent")

    else:
      # Error return code
      self._handle_return_code(return_code, message, command, request_id)
      # Should not reach here (handle_return_code raises), but just in case:
      raise RuntimeError(f"Command {command} failed: {return_code} {message}")
