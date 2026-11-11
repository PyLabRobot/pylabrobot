from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
  Any,
  Awaitable,
  Callable,
  Dict,
  Literal,
  Mapping,
  Optional,
  Protocol,
  TypeVar,
  Union,
)

from .file_transfer_control import ProtocolDeletionPendingError, _FileTransferControl
from .ht200 import BTXHT200
from .standard import (
  ElectroporationCancellationDetails,
  ElectroporationCancellationResult,
  ElectroporationCleanup,
  ElectroporationExecutionDetails,
  ElectroporationLogCapture,
  ElectroporationPreparationDetails,
  ElectroporationProtocol,
  ElectroporationRunResult,
  PreparedElectroporationRun,
)
from .the_ghost_touch import (
  CancelledPreparedUserProtocolResult,
  PreparedUserProtocolResult,
  StartedPreparedUserProtocolResult,
  _TheGhostTouch,
)

logger = logging.getLogger(__name__)

PlateHandlerResetState = Literal["unknown", "reset_confirmed", "continue_current_position"]


class _GhostTouchSession(Protocol):
  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def ensure_home(self) -> Any:
    pass

  async def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> PreparedUserProtocolResult:
    pass

  async def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> StartedPreparedUserProtocolResult:
    pass

  async def cancel_prepared_user_protocol(self) -> CancelledPreparedUserProtocolResult:
    pass


GhostTouchResult = TypeVar("GhostTouchResult")


@dataclass(frozen=True)
class TemporaryProtocolCleanupResult:
  delete_result: Optional[Dict[str, Any]]
  delete_retry_used: bool
  delete_error: Optional[str]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "delete_result": self.delete_result,
      "delete_retry_used": self.delete_retry_used,
      "delete_error": self.delete_error,
    }

  def to_cleanup(self) -> ElectroporationCleanup:
    deleted = None if self.delete_result is None else self.delete_result.get("deleted")
    return ElectroporationCleanup(
      deleted=deleted if isinstance(deleted, bool) else None,
      retry_used=self.delete_retry_used,
      error=self.delete_error,
      details=self.as_dict(),
    )


@dataclass(frozen=True)
class MatchedRunLogResult:
  before_count: int
  after_count: int
  new_log_paths: tuple[str, ...]
  matched_log_path: Optional[str]
  matched_log: Optional[Dict[str, Any]]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "before_count": self.before_count,
      "after_count": self.after_count,
      "new_log_paths": list(self.new_log_paths),
      "matched_log_path": self.matched_log_path,
      "matched_log": self.matched_log,
    }


class BTXGeminiX2:
  """BTX Gemini X2 driver.

  The driver owns both mutually exclusive serial modes used by the device: Protocol Manager
  file transfer and the RSI touchscreen workflow. All device operations are serialized so the
  two modes cannot be used concurrently.
  """

  UI_PROTOCOL_NAME_BYTES = _FileTransferControl.UI_PROTOCOL_NAME_BYTES
  DEFAULT_TEMPORARY_PROTOCOL_PREFIX = "!PLR"
  PLATE_HANDLER_RESET_STATE_UNKNOWN: PlateHandlerResetState = "unknown"
  PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED: PlateHandlerResetState = "reset_confirmed"
  PLATE_HANDLER_RESET_STATE_CONTINUE_CURRENT_POSITION: PlateHandlerResetState = (
    "continue_current_position"
  )
  PLATE_HANDLER_RESET_STATES = {
    PLATE_HANDLER_RESET_STATE_UNKNOWN,
    PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
    PLATE_HANDLER_RESET_STATE_CONTINUE_CURRENT_POSITION,
  }
  LOG_POLL_TIMEOUT_SECONDS = 10.0
  LOG_POLL_INTERVAL_SECONDS = 0.5

  def __init__(
    self,
    port: Optional[str] = None,
    *,
    plate_handler: Optional[BTXHT200] = None,
    temporary_protocol_prefix: str = DEFAULT_TEMPORARY_PROTOCOL_PREFIX,
  ) -> None:
    self._file_transfer_control = _FileTransferControl(port=port)
    self.plate_handler = plate_handler if plate_handler is not None else BTXHT200()
    self._temporary_protocol_prefix = temporary_protocol_prefix
    self._operation_lock = asyncio.Lock()
    self._is_setup = False

  @property
  def port(self) -> Optional[str]:
    """The resolved serial port, if setup has discovered one."""
    return self._file_transfer_control.port

  async def setup(self) -> None:
    async with self._operation_lock:
      if self._is_setup:
        return
      logger.info("Setting up BTX Gemini X2")
      try:
        await self._file_transfer_control.setup()
        await self._ensure_temporary_protocol_prefix_order_safe(self._temporary_protocol_prefix)
      except BaseException:
        await self._stop_file_transfer_after_failure()
        raise
      self._is_setup = True
      logger.info("BTX Gemini X2 ready on port %s", self.port)

  async def stop(self) -> None:
    async with self._operation_lock:
      if not self._is_setup:
        return
      logger.info("Stopping BTX Gemini X2")
      try:
        await self._file_transfer_control.stop()
      finally:
        self._is_setup = False
      logger.info("BTX Gemini X2 stopped")

  async def _stop_file_transfer_after_failure(self) -> None:
    try:
      await self._file_transfer_control.stop()
    except Exception:
      logger.exception("Failed to close Gemini X2 after setup failure")

  def _require_setup(self) -> None:
    if not self._is_setup:
      raise RuntimeError("BTX Gemini X2 is not set up. Call setup() first.")

  async def list_protocols(self) -> list[str]:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.list_protocols()

  async def request_protocol(self, protocol_name: str) -> Dict[str, Any]:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.request_protocol(protocol_name)

  async def add_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol,
    overwrite: bool = False,
  ) -> Dict[str, Any]:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.add_protocol(
        protocol_name, protocol, overwrite=overwrite
      )

  async def delete_protocol(
    self,
    protocol_name: str,
    missing_ok: bool = False,
  ) -> Dict[str, Any]:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.delete_protocol(protocol_name, missing_ok=missing_ok)

  async def list_log_files(self, root: str = "\\BTXDATA") -> list[str]:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.list_log_files(root=root)

  async def fetch_sd_file(self, sd_path: str) -> str:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.fetch_sd_file(sd_path)

  async def request_version(self) -> str:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.request_version()

  async def request_serial_number(self) -> str:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.request_serial_number()

  async def request_device_time(self) -> str:
    async with self._operation_lock:
      self._require_setup()
      return await self._file_transfer_control.request_device_time()

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    return self._file_transfer_control.parse_run_log(text)

  async def _run_with_ghost_touch(
    self,
    action: Callable[[_GhostTouchSession], Awaitable[GhostTouchResult]],
  ) -> GhostTouchResult:
    """Temporarily hand the device port to RSI control while holding the device lock."""
    self._require_setup()
    port = self.port
    if port is None:
      raise RuntimeError("Gemini X2 serial port is not resolved. Call setup() first.")

    logger.info("Switching Gemini X2 from file-transfer control to touchscreen control")
    try:
      await self._file_transfer_control.stop()
    except BaseException:
      self._is_setup = False
      raise
    ghost_touch: _GhostTouchSession = _TheGhostTouch(port=port)
    primary_error: BaseException | None = None
    stop_error: Exception | None = None
    restore_error: Exception | None = None
    try:
      await ghost_touch.setup()
      result = await action(ghost_touch)
    except BaseException as exc:
      primary_error = exc
    finally:
      try:
        await ghost_touch.stop()
      except Exception as exc:
        stop_error = exc
        logger.exception("Failed to stop Gemini X2 touchscreen control")
      try:
        logger.info("Restoring Gemini X2 file-transfer control")
        await self._file_transfer_control.setup()
      except Exception as exc:
        restore_error = exc
        self._is_setup = False
        logger.exception("Failed to restore Gemini X2 file-transfer control")

    if primary_error is not None:
      raise primary_error.with_traceback(primary_error.__traceback__)
    if restore_error is not None:
      raise restore_error
    if stop_error is not None:
      raise stop_error
    return result

  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    plate_handler_reset_state: PlateHandlerResetState = "unknown",
  ) -> PreparedElectroporationRun:
    """Create a temporary protocol and leave the Gemini armed on ``Run Protocol``."""
    async with self._operation_lock:
      self._require_setup()
      if plate_columns is not None and (
        isinstance(plate_columns, bool) or not 0 <= plate_columns <= 12
      ):
        raise ValueError("plate_columns must be in the range 0..12.")
      resolved_prefix = self._temporary_protocol_prefix if prefix is None else prefix
      resolved_reset_state = self._resolve_plate_handler_reset_state(
        plate_columns=plate_columns,
        plate_handler_reset_state=plate_handler_reset_state,
      )
      assumed_pulse_count, assumed_column_adjust = self._resolve_plate_handler_manual_state(
        plate_columns=plate_columns
      )
      await self._ensure_temporary_protocol_prefix_available(resolved_prefix)

      baseline_log_paths = tuple(await self._file_transfer_control.list_log_files())
      device_serial_number = await self._file_transfer_control.request_serial_number()
      protocol_name = self._make_temporary_protocol_name(resolved_prefix)
      logger.info(
        "Preparing Gemini X2 temporary protocol %s (plate_columns=%s)",
        protocol_name,
        plate_columns,
      )
      add_result = await self._file_transfer_control.add_protocol(
        protocol_name=protocol_name,
        protocol=protocol,
        overwrite=False,
      )

      try:
        rsi_result = await self._run_with_ghost_touch(
          lambda ghost_touch: ghost_touch.prepare_user_protocol(
            protocol_name=protocol_name,
            plate_columns=plate_columns,
          )
        )
      except BaseException:
        await self._cleanup_temporary_protocol(protocol_name, missing_ok=True)
        raise

      return PreparedElectroporationRun(
        protocol_name=protocol_name,
        device_serial_number=device_serial_number,
        protocol=protocol,
        plate_columns=plate_columns,
        prefix=resolved_prefix,
        prepared_at_utc=self._now_utc_iso(),
        baseline_log_paths=baseline_log_paths,
        prepare_result=ElectroporationPreparationDetails(
          prepared_state=rsi_result.prepared_verification.state,
          protocol_setup=add_result,
          device_prepare={
            "plate_handler_reset_state": resolved_reset_state,
            "assumed_plate_handler_pulse_count": assumed_pulse_count,
            "assumed_plate_handler_column_adjust": assumed_column_adjust,
            **rsi_result.as_dict(),
          },
        ),
      )

  async def start_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> ElectroporationRunResult:
    """Verify, start, and collect the result for a previously prepared temporary run."""
    async with self._operation_lock:
      self._require_setup()
      if max_run_seconds <= 0:
        raise ValueError("max_run_seconds must be greater than zero.")
      prepared = self._coerce_prepared_run(prepared_run)
      await self._verify_prepared_run_identity(prepared, verify_protocol=True)

      logger.info("Starting prepared Gemini X2 run for protocol %s", prepared.protocol_name)
      started_at_utc = self._now_utc_iso()
      rsi_result = await self._run_with_ghost_touch(
        lambda ghost_touch: ghost_touch.start_prepared_user_protocol(
          protocol_name=prepared.protocol_name,
          home_after=home_after,
          max_run_seconds=max_run_seconds,
        )
      )
      completed_at_utc = rsi_result.completed_at_utc

      try:
        log_capture = await self._collect_matching_new_log(
          before_logs=set(prepared.baseline_log_paths),
          protocol_name=prepared.protocol_name,
        )
      finally:
        cleanup = await self._cleanup_temporary_protocol(prepared.protocol_name, missing_ok=True)

      summary: Dict[str, Any] = {}
      if log_capture.matched_log is not None:
        parsed_summary = log_capture.matched_log.get("summary")
        if isinstance(parsed_summary, Mapping):
          summary = dict(parsed_summary)
      return ElectroporationRunResult(
        prepared_run=prepared,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        rsi_result=ElectroporationExecutionDetails(
          verification_state=rsi_result.verification.state,
          completed_state=rsi_result.completed.state,
          final_state=(
            rsi_result.completed.state if rsi_result.home is None else rsi_result.home.state
          ),
          device_run=rsi_result.as_dict(),
        ),
        log_capture=ElectroporationLogCapture(
          matched_log_path=log_capture.matched_log_path,
          summary=summary,
          details=log_capture.as_dict(),
        ),
        cleanup=cleanup.to_cleanup(),
      )

  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
  ) -> ElectroporationCancellationResult:
    """Return the Gemini home and delete the prepared temporary protocol."""
    async with self._operation_lock:
      self._require_setup()
      prepared = self._coerce_prepared_run(prepared_run)
      await self._verify_prepared_run_identity(prepared, verify_protocol=False)

      logger.info("Cancelling prepared Gemini X2 run for protocol %s", prepared.protocol_name)
      rsi_result = await self._run_with_ghost_touch(
        lambda ghost_touch: ghost_touch.cancel_prepared_user_protocol()
      )
      cleanup = await self._cleanup_temporary_protocol(prepared.protocol_name, missing_ok=True)

      return ElectroporationCancellationResult(
        prepared_run=prepared,
        cancelled_at_utc=self._now_utc_iso(),
        rsi_result=ElectroporationCancellationDetails(
          final_state=rsi_result.final_state.state,
          device_cancel=rsi_result.as_dict(),
        ),
        cleanup=cleanup.to_cleanup(),
      )

  async def request_device_info(self) -> Dict[str, Any]:
    """Return Gemini identity plus the supported electroporation workflow surface."""
    async with self._operation_lock:
      self._require_setup()
      version = await self._file_transfer_control.request_version()
      serial_number = await self._file_transfer_control.request_serial_number()
      device_time = await self._file_transfer_control.request_device_time()
      protocols = await self._file_transfer_control.list_protocols()
      return {
        "device": self.__class__.__name__,
        "model": "Gemini X2",
        "port": self.port,
        "version": version,
        "serial_number": serial_number,
        "device_time": device_time,
        "protocol_count": len(protocols),
        "supports_prepared_temporary_runs": True,
        "supports_serialized_prepared_runs": True,
        "supports_stored_protocol_runs": False,
        "supports_plate_columns": True,
        "supports_plate_handler_reset_state": True,
        "plate_handler_reset_states": sorted(self.PLATE_HANDLER_RESET_STATES),
        "plate_handler": self.plate_handler.get_device_info(),
        "temporary_protocol_prefix": self._temporary_protocol_prefix,
      }

  async def _verify_prepared_run_identity(
    self,
    prepared: PreparedElectroporationRun,
    *,
    verify_protocol: bool,
  ) -> None:
    current_serial_number = await self._file_transfer_control.request_serial_number()
    if current_serial_number != prepared.device_serial_number:
      raise RuntimeError(
        "Prepared Gemini X2 run belongs to serial number "
        f"{prepared.device_serial_number!r}, but the connected device is "
        f"{current_serial_number!r}."
      )
    if verify_protocol:
      await self._file_transfer_control.verify_protocol(prepared.protocol_name, prepared.protocol)

  def _resolve_plate_handler_reset_state(
    self,
    *,
    plate_columns: Optional[int],
    plate_handler_reset_state: PlateHandlerResetState,
  ) -> PlateHandlerResetState:
    if plate_handler_reset_state not in self.PLATE_HANDLER_RESET_STATES:
      allowed = ", ".join(sorted(self.PLATE_HANDLER_RESET_STATES))
      raise ValueError(
        f"Unsupported plate_handler_reset_state={plate_handler_reset_state!r}. Allowed: {allowed}."
      )
    if plate_columns is None:
      if plate_handler_reset_state != self.PLATE_HANDLER_RESET_STATE_UNKNOWN:
        raise ValueError("plate_handler_reset_state is only valid when plate_columns is set.")
      return plate_handler_reset_state
    if plate_handler_reset_state == self.PLATE_HANDLER_RESET_STATE_UNKNOWN:
      raise ValueError(
        "plate_columns requires an explicit plate_handler_reset_state. Use "
        "'reset_confirmed' after manually lid-cycling the HT-200 back to column 1, "
        "or 'continue_current_position' to intentionally continue from the current handler position."
      )
    return plate_handler_reset_state

  def _resolve_plate_handler_manual_state(
    self,
    *,
    plate_columns: Optional[int],
  ) -> tuple[Optional[int], Optional[int]]:
    if plate_columns is None:
      return None, None
    return self.plate_handler.require_manual_state()

  async def _ensure_temporary_protocol_prefix_order_safe(self, prefix: str) -> None:
    conflicts = self._temporary_protocol_preceding_conflicts(
      await self._file_transfer_control.list_protocols(),
      prefix,
    )
    if conflicts:
      reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
      raise RuntimeError(
        "Temporary protocol prefix "
        f"{prefix!r} is not safe on this device. These user protocols would sort before "
        f"{reserved_anchor!r}: {conflicts}. Remove/rename them before setup or choose "
        "a different reserved prefix."
      )

  async def _ensure_temporary_protocol_prefix_available(self, prefix: str) -> None:
    protocols = await self._file_transfer_control.list_protocols()
    preceding = self._temporary_protocol_preceding_conflicts(protocols, prefix)
    collisions = self._temporary_protocol_prefix_collisions(protocols, prefix)
    conflicts = sorted(set(preceding + collisions), key=str.casefold)
    if conflicts:
      reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
      raise RuntimeError(
        "Temporary protocol prefix "
        f"{prefix!r} is not available on this device. These user protocols would sort before "
        f"or collide with {reserved_anchor!r}: {conflicts}. Remove/rename them before "
        "preparing a temporary protocol or choose a different reserved prefix."
      )

  def _temporary_protocol_sort_anchor(self, prefix: str) -> str:
    prefix_text = prefix.strip()
    if len(prefix_text) == 0:
      raise ValueError("prefix cannot be empty.")
    try:
      prefix_text.encode("ascii")
    except UnicodeEncodeError as exc:
      raise ValueError("prefix must be ASCII.") from exc
    return f"{prefix_text}_"

  def _temporary_protocol_preceding_conflicts(
    self,
    protocols: list[str],
    prefix: str,
  ) -> list[str]:
    anchor_key = self._temporary_protocol_sort_anchor(prefix).casefold()
    return sorted(
      (name for name in protocols if name.casefold() < anchor_key),
      key=str.casefold,
    )

  def _temporary_protocol_prefix_collisions(
    self,
    protocols: list[str],
    prefix: str,
  ) -> list[str]:
    anchor_key = self._temporary_protocol_sort_anchor(prefix).casefold()
    return sorted(
      (name for name in protocols if name.casefold().startswith(anchor_key)),
      key=str.casefold,
    )

  def _coerce_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
  ) -> PreparedElectroporationRun:
    if isinstance(prepared_run, PreparedElectroporationRun):
      return prepared_run
    return PreparedElectroporationRun.from_dict(prepared_run)

  async def _force_home_via_ghost_touch(self) -> None:
    await self._run_with_ghost_touch(lambda ghost_touch: ghost_touch.ensure_home())

  async def _cleanup_temporary_protocol(
    self,
    protocol_name: str,
    *,
    missing_ok: bool,
  ) -> TemporaryProtocolCleanupResult:
    delete_result: Dict[str, Any] | None = None
    delete_error: str | None = None
    delete_retry_used = False

    try:
      delete_result = await self._file_transfer_control.delete_protocol(
        protocol_name,
        missing_ok=missing_ok,
      )
    except ProtocolDeletionPendingError:
      delete_retry_used = True
      logger.warning(
        "Gemini X2 protocol %s remained after deletion; returning the touchscreen home and retrying",
        protocol_name,
      )
      try:
        await self._force_home_via_ghost_touch()
        delete_result = await self._file_transfer_control.delete_protocol(
          protocol_name,
          missing_ok=missing_ok,
        )
      except Exception as retry_exc:  # pragma: no cover - hardware-specific recovery
        delete_error = str(retry_exc)
        logger.exception("Failed to delete Gemini X2 temporary protocol %s", protocol_name)
    except Exception as exc:  # pragma: no cover - hardware-specific recovery
      delete_error = str(exc)
      logger.exception("Failed to delete Gemini X2 temporary protocol %s", protocol_name)

    return TemporaryProtocolCleanupResult(
      delete_result=delete_result,
      delete_retry_used=delete_retry_used,
      delete_error=delete_error,
    )

  async def _collect_matching_new_log(
    self,
    before_logs: set[str],
    protocol_name: str,
  ) -> MatchedRunLogResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + self.LOG_POLL_TIMEOUT_SECONDS
    after_logs = set(before_logs)
    new_logs: list[str] = []
    while True:
      after_logs = set(await self._file_transfer_control.list_log_files())
      new_logs = sorted(after_logs - before_logs)
      for log_path in new_logs:
        text = await self._file_transfer_control.fetch_sd_file(log_path)
        parsed = self.parse_run_log(text)
        summary = parsed.get("summary")
        if isinstance(summary, Mapping) and summary.get("protocol_name") == protocol_name:
          return MatchedRunLogResult(
            before_count=len(before_logs),
            after_count=len(after_logs),
            new_log_paths=tuple(new_logs),
            matched_log_path=log_path,
            matched_log=parsed,
          )
      if loop.time() >= deadline:
        logger.warning(
          "No new Gemini X2 run log matched protocol %s within %.1f seconds",
          protocol_name,
          self.LOG_POLL_TIMEOUT_SECONDS,
        )
        return MatchedRunLogResult(
          before_count=len(before_logs),
          after_count=len(after_logs),
          new_log_paths=tuple(new_logs),
          matched_log_path=None,
          matched_log=None,
        )
      await asyncio.sleep(self.LOG_POLL_INTERVAL_SECONDS)

  def _make_temporary_protocol_name(self, prefix: str) -> str:
    reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
    remaining_bytes = self.UI_PROTOCOL_NAME_BYTES - len(reserved_anchor.encode("ascii"))
    if remaining_bytes < 6:
      raise ValueError(
        "Generated temp protocol name would exceed the "
        f"{self.UI_PROTOCOL_NAME_BYTES}-byte Gemini UI limit. Shorten prefix={prefix!r}."
      )
    return f"{reserved_anchor}{uuid.uuid4().hex[:remaining_bytes].upper()}"

  def _now_utc_iso(self) -> str:
    return datetime.now(timezone.utc).isoformat()
