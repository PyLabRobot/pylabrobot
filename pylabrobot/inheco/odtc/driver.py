"""ODTCDriver — the ODTC SiLA communication layer and v1b1 Driver.

Extends InhecoSiLAInterface (HTTP server, SOAP encode/decode, async command
queueing) with ODTC-specific event handling, then satisfies the v1b1 Driver
interface by mapping setup()/stop() to InhecoSiLAInterface.start()/close().

Three-channel error model:
- ResponseEvent (non-success code)  → SiLAError
- ErrorEvent                        → SiLAError  [was RuntimeError — fixed]
- StatusEvent(errorHandling/inError)→ SiLAError from structured firmware extensions
  Extensions: [0]=ErrorClassification, [1]=InternalErrorCode, [2]=HexCode,
               [3]=ErrorName, [4]=ErrorDescription

mcDuration from SOAP responses drives per-command timeouts instead of a
fixed 3-hour ceiling.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Set

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.device import Driver
from pylabrobot.inheco.scila.inheco_sila_interface import (
  InhecoSiLAInterface,
  SiLAError,
  SiLAState,
)

from .protocol import build_progress_from_data_event

# Minimum timeout for async commands regardless of mcDuration (seconds)
_MIN_COMMAND_TIMEOUT: float = 300.0
# Safety multiplier applied to device-reported mcDuration
_MC_DURATION_SAFETY_MULTIPLIER: float = 1.5

# Device-specific return codes that put the device into InError state
DEVICE_ERROR_CODES: Set[int] = {1000, 2000, 2001, 2007}

# States that indicate a device error in progress
_ERROR_STATES: set = {SiLAState.ERRORHANDLING, SiLAState.INERROR}


class ODTCDriver(InhecoSiLAInterface, Driver):
  """ODTC SiLA communication layer, satisfying the v1b1 Driver interface.

  Inherits the full HTTP/SOAP/async-command infrastructure from
  InhecoSiLAInterface and adds ODTC-specific event handling. The v1b1
  Driver contract (setup/stop) maps to InhecoSiLAInterface.start/close.
  """

  def __init__(
    self,
    machine_ip: str,
    client_ip: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
  ) -> None:
    InhecoSiLAInterface.__init__(self, machine_ip=machine_ip, client_ip=client_ip, logger=logger)
    Driver.__init__(self)

  # ------------------------------------------------------------------
  # v1b1 Driver lifecycle
  # ------------------------------------------------------------------

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Start the SiLA HTTP event-receiver server."""
    await self.start()

  async def stop(self) -> None:
    """Shut down the SiLA HTTP event-receiver server."""
    await self.close()

  # ------------------------------------------------------------------
  # Status event — primary error notification channel
  # ------------------------------------------------------------------

  def _on_status_event(self, status_event: dict) -> None:
    """Handle StatusEvent: log state and reject pending futures on error states.

    The ODTC firmware uses StatusEvent as its primary error notification path.
    The eventDescription may carry structured error fields in Extensions:
      [0] = ErrorClassification (e.g. "DeviceError")
      [1] = InternalErrorCode   (int, e.g. 2001 = motor error)
      [2] = InternalErrorCodeHex
      [3] = ErrorName           (e.g. "MotorError")
      [4] = ErrorDescription    (human-readable detail)
    """
    import xml.etree.ElementTree as ET

    event_description = status_event.get("eventDescription", {})
    device_state: Optional[str] = None

    if isinstance(event_description, dict):
      device_state = event_description.get("DeviceState")
      extensions = event_description.get("Extensions") or event_description.get("extensions") or []
    elif isinstance(event_description, str) and "<DeviceState>" in event_description:
      root = ET.fromstring(event_description)
      device_state = root.text if root.tag == "DeviceState" else root.findtext("DeviceState")
      extensions = []
    else:
      self._logger.warning(f"StatusEvent with unparsable eventDescription: {event_description!r}")
      return

    if device_state:
      self._logger.debug(f"StatusEvent device state: {device_state}")

    # Parse structured error extensions (ODTC firmware error fields)
    error_classification = ""
    internal_error_code = 0
    internal_error_code_hex = ""
    error_name = ""
    error_description = ""
    if isinstance(extensions, (list, tuple)):
      try:
        error_classification = str(extensions[0]) if len(extensions) > 0 else ""
        internal_error_code = int(extensions[1]) if len(extensions) > 1 else 0
        internal_error_code_hex = str(extensions[2]) if len(extensions) > 2 else ""
        error_name = str(extensions[3]) if len(extensions) > 3 else ""
        error_description = str(extensions[4]) if len(extensions) > 4 else ""
      except (IndexError, ValueError, TypeError):
        pass

    if error_name or error_description or error_classification:
      self._logger.error(
        "StatusEvent error: classification=%r code=%d (%s) name=%r desc=%r",
        error_classification, internal_error_code, internal_error_code_hex,
        error_name, error_description,
      )

    # Reject all pending futures when device enters an error state
    try:
      state = SiLAState(device_state) if device_state else None
    except ValueError:
      state = None

    if state in _ERROR_STATES and self._pending_by_id:
      msg = (
        error_description or error_name
        or f"Device entered {device_state} state"
      )
      if internal_error_code:
        msg = f"{msg} [code {internal_error_code}]"
      if state == SiLAState.INERROR:
        msg += ". Device requires a power cycle to recover."

      for request_id in list(self._pending_by_id.keys()):
        pending = self._pending_by_id.get(request_id)
        if pending and not pending.fut.done():
          self._complete_pending(
            request_id,
            exception=SiLAError(internal_error_code or 9, msg, pending.name),
          )

  # ------------------------------------------------------------------
  # Error event — secondary error notification channel
  # ------------------------------------------------------------------

  def _on_error_event(self, error_event: dict) -> None:
    """Handle ErrorEvent with typed SiLAError (was RuntimeError in base — fixed)."""
    req_id = error_event.get("requestId")
    return_value = error_event.get("returnValue", {})
    return_code = return_value.get("returnCode") or 0
    message = return_value.get("message", "")

    self._logger.error(
      "ErrorEvent for requestId %s: code %s, message: %s",
      req_id, return_code, message,
    )

    err_msg = message.replace("\n", " ") if message else f"Error (code {return_code})"
    if req_id is not None:
      pending = self._pending_by_id.get(req_id)
      if pending and not pending.fut.done():
        self._complete_pending(
          req_id,
          exception=SiLAError(return_code, err_msg, pending.name),
        )

  # ------------------------------------------------------------------
  # Response event — code 1 fix
  # ------------------------------------------------------------------

  def _on_response_event(self, response_event: dict) -> None:
    """Handle ResponseEvent: code 1 = success (no data), code 3 = success (with data)."""
    import xml.etree.ElementTree as ET

    request_id = response_event.get("requestId")
    if request_id is None:
      self._logger.warning("ResponseEvent missing requestId")
      return

    pending = self._pending_by_id.get(request_id)
    if pending is None:
      self._logger.warning(f"ResponseEvent for unknown requestId: {request_id}")
      return
    if pending.fut.done():
      self._logger.warning(f"ResponseEvent for already-completed requestId: {request_id}")
      return

    return_value = response_event.get("returnValue", {})
    return_code = return_value.get("returnCode")

    if return_code == 1:
      self._complete_pending(request_id, result=None)
    elif return_code == 3:
      response_data = response_event.get("responseData", "")
      if response_data and response_data.strip():
        try:
          self._complete_pending(request_id, result=ET.fromstring(response_data))
        except ET.ParseError as e:
          self._logger.error(f"Failed to parse ResponseEvent responseData: {e}")
          self._complete_pending(
            request_id, exception=RuntimeError(f"Failed to parse response data: {e}")
          )
      else:
        self._complete_pending(request_id, result=None)
    else:
      message = return_value.get("message", "")
      err_msg = message.replace("\n", " ") if message else f"Unknown error (code {return_code})"
      self._complete_pending(
        request_id,
        exception=SiLAError(return_code, err_msg, pending.name),
      )

  # ------------------------------------------------------------------
  # mcDuration-driven timeout
  # ------------------------------------------------------------------

  def _timeout_from_mc_duration(self, mc_duration_s: Optional[float]) -> float:
    """Compute per-command timeout from device-reported mcDuration."""
    if mc_duration_s and mc_duration_s > 0:
      return max(mc_duration_s * _MC_DURATION_SAFETY_MULTIPLIER, _MIN_COMMAND_TIMEOUT)
    return _MIN_COMMAND_TIMEOUT

  # ------------------------------------------------------------------
  # Device-specific return codes (1000+)
  # ------------------------------------------------------------------

  def _handle_device_error_code(self, return_code: int, message: str, command_name: str) -> None:
    """All ODTC device-specific codes (1000+) raise SiLAError."""
    raise SiLAError(return_code, f"Device error: {message}", command_name)

  # ------------------------------------------------------------------
  # DataEvent
  # ------------------------------------------------------------------

  def _on_data_event(self, data_event: dict) -> None:
    super()._on_data_event(data_event)
    try:
      progress = build_progress_from_data_event(data_event, None)
      self._logger.debug(
        "DataEvent requestId %s: elapsed %.0fs, block %.1f°C, target %.1f°C, lid %.1f°C",
        data_event.get("requestId"),
        progress.elapsed_s,
        progress.current_temp_c or 0.0,
        progress.target_temp_c or 0.0,
        progress.lid_temp_c or 0.0,
      )
    except Exception:
      pass  # DataEvent parsing failures must not interrupt event handling
