"""ODTC-specific SiLA interface with parallelism, lockId validation, and return code handling.

This module extends InhecoSiLAInterface to support ODTC-specific requirements:
- Multiple in-flight commands with parallelism enforcement
- LockId validation (defaults to None, validates when device is locked)
- Proper return code handling (including device-specific codes)
- All event types (ResponseEvent, StatusEvent, DataEvent, ErrorEvent)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from pylabrobot.storage.inheco.scila.inheco_sila_interface import (
  InhecoSiLAInterface,
  SiLAError,
  SiLAState,
  SiLATimeoutError,
)

from .odtc_model import ODTCProgress


class FirstEventTimeout(SiLATimeoutError):
  """No first event received within timeout (e.g. no DataEvent for ExecuteMethod)."""

  pass


# Default max wait for async command completion (3 hours). SiLA2-aligned: protocol execution always bounded.
DEFAULT_LIFETIME_OF_EXECUTION: float = 10800.0

# Default timeout for waiting for first DataEvent (ExecuteMethod) and default lifetime/eta for status-driven commands.
DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS: float = 60.0

# Delay (seconds) after command start before starting GetStatus polling loop.
# Kept as module constant for backward compatibility with ODTCBackend.
POLLING_START_BUFFER: float = 10.0


class ODTCSiLAInterface(InhecoSiLAInterface):
  """ODTC-specific SiLA interface with parallelism, lockId validation, and return code handling.

  Extends InhecoSiLAInterface to support:
  - Multiple in-flight commands with parallelism enforcement per ODTC doc section 3
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

  # State allowability table from ODTC doc section 4 (reference only; device enforces this)
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
      SiLAState.ERRORHANDLING,
      SiLAState.INERROR,
    },
    "GetLastData": {SiLAState.IDLE, SiLAState.BUSY},
    "GetStatus": set(SiLAState),
    "Initialize": {SiLAState.STANDBY},
    "LockDevice": {SiLAState.STANDBY},
    "OpenDoor": {SiLAState.IDLE, SiLAState.BUSY},
    "Pause": {SiLAState.IDLE, SiLAState.BUSY},
    "ReadActualTemperature": {SiLAState.IDLE, SiLAState.BUSY},
    "Reset": set(SiLAState),
    "SetConfiguration": {SiLAState.STANDBY},
    "SetParameters": {SiLAState.STANDBY, SiLAState.IDLE, SiLAState.BUSY},
    "StopMethod": {SiLAState.IDLE, SiLAState.BUSY},
    "UnlockDevice": {SiLAState.STANDBY},
  }

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

    # Lock ID tracking per pending request (for LockDevice)
    self._pending_lock_ids: Dict[int, str] = {}

    # DataEvent storage by request_id
    self._data_events_by_request_id: Dict[int, List[Dict[str, Any]]] = {}

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
    """ODTC cleanup (lock state) then delegate to base."""
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

    super()._complete_pending(request_id, result=result, exception=exception)

  def _handle_device_error_code(self, return_code: int, message: str, command_name: str) -> None:
    """Handle ODTC device-specific return codes (1000+)."""
    if return_code in self.DEVICE_ERROR_CODES:
      raise SiLAError(return_code, f"Device error: {message}", command_name)
    # Warning or recoverable error
    self._logger.warning(
      f"Command {command_name} returned device-specific code {return_code}: {message}"
    )

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

  async def send_command(
    self,
    command: str,
    request_id: Optional[int] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
  ) -> Any:
    # Auto-inject lockId when device is locked
    if self._lock_id is not None and "lockId" not in kwargs:
      kwargs["lockId"] = self._lock_id

    if timeout is None:
      timeout = DEFAULT_FIRST_EVENT_TIMEOUT_SECONDS
    try:
      return await asyncio.wait_for(
        super().send_command(command, request_id=request_id, **kwargs), timeout=timeout
      )
    except asyncio.TimeoutError:
      raise SiLATimeoutError(
        f"Timed out after {timeout}s waiting for ResponseEvent", command=command
      ) from None
