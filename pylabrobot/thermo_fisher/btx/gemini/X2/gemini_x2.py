from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
  Any,
  Callable,
  Dict,
  Mapping,
  Optional,
  Protocol,
  TypeVar,
  Union,
  cast,
  runtime_checkable,
)

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.electroporation import (
  Electroporation,
  ElectroporationBackend,
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
from pylabrobot.device import Device, Driver

from .file_transfer_control import FileTransferControl
from .ht200 import BTXHT200
from .the_ghost_touch import (
  CancelledPreparedUserProtocolResult,
  PreparedUserProtocolResult,
  StartedPreparedUserProtocolResult,
  TheGhostTouch,
)


@runtime_checkable
class _GhostTouchSession(Protocol):
  def __enter__(self) -> "_GhostTouchSession":
    pass

  def __exit__(self, exc_type, exc, tb) -> None:
    pass

  def ensure_home(self) -> Any:
    pass

  def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> PreparedUserProtocolResult:
    pass

  def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> StartedPreparedUserProtocolResult:
    pass

  def cancel_prepared_user_protocol(
    self, home_after: bool = True
  ) -> CancelledPreparedUserProtocolResult:
    pass


GhostTouchFactory = Callable[..., _GhostTouchSession]
GhostTouchResult = TypeVar("GhostTouchResult")


def _result_dict(value: Any) -> Dict[str, Any]:
  if hasattr(value, "as_dict"):
    return cast(Dict[str, Any], value.as_dict())
  return cast(Dict[str, Any], value)


def _nested_state(payload: Mapping[str, Any], *path: str) -> Optional[str]:
  current: Any = payload
  for key in path:
    if not isinstance(current, Mapping):
      return None
    current = current.get(key)
  if isinstance(current, str):
    return current
  return None


@dataclass(frozen=True)
class TemporaryProtocolCleanupResult:
  delete_result: Any
  delete_retry_used: bool
  delete_error: Optional[str]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "delete_result": self.delete_result,
      "delete_retry_used": self.delete_retry_used,
      "delete_error": self.delete_error,
    }


@dataclass(frozen=True)
class MatchedRunLogResult:
  before_count: int
  after_count: int
  new_log_paths: tuple[str, ...]
  matched_log_path: Optional[str]
  matched_log: Any

  def as_dict(self) -> Dict[str, Any]:
    return {
      "before_count": self.before_count,
      "after_count": self.after_count,
      "new_log_paths": list(self.new_log_paths),
      "matched_log_path": self.matched_log_path,
      "matched_log": self.matched_log,
    }


class BTXGeminiX2Driver(Driver):
  """BTX Gemini X2 driver.

  Owns the file-transfer connection lifecycle and the temporary handoff into the RSI touch
  control session.
  """

  def __init__(
    self,
    port: Optional[str] = None,
    *,
    file_transfer_control: Optional[FileTransferControl] = None,
    ghost_touch_factory: Optional[GhostTouchFactory] = None,
    ghost_touch_kwargs: Optional[dict[str, Any]] = None,
  ) -> None:
    super().__init__()
    self.port = port or (file_transfer_control.port if file_transfer_control is not None else None)
    self.file_transfer_control = file_transfer_control or FileTransferControl(port=port)
    self._ghost_touch_factory = ghost_touch_factory or TheGhostTouch
    self._ghost_touch_kwargs = dict(ghost_touch_kwargs or {})

  async def setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
    await self.file_transfer_control.setup()
    if self.file_transfer_control.port is not None:
      self.port = self.file_transfer_control.port

  async def stop(self):
    await self.file_transfer_control.stop()

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.port,
    }

  async def list_protocols(self) -> list[str]:
    return await self.file_transfer_control.list_protocols()

  async def get_protocol(self, protocol_name: str) -> Dict[str, Any]:
    return await self.file_transfer_control.get_protocol(protocol_name)

  async def add_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol,
    overwrite: bool = False,
  ) -> Dict[str, Any]:
    return await self.file_transfer_control.add_protocol(
      protocol_name, protocol, overwrite=overwrite
    )

  async def delete_protocol(self, protocol_name: str, missing_ok: bool = False) -> Dict[str, Any]:
    return await self.file_transfer_control.delete_protocol(protocol_name, missing_ok=missing_ok)

  async def list_log_files(self, root: str = "\\BTXDATA") -> list[str]:
    return await self.file_transfer_control.list_log_files(root=root)

  async def fetch_sd_file(self, sd_path: str) -> str:
    return await self.file_transfer_control.fetch_sd_file(sd_path)

  async def get_version(self) -> str:
    return await self.file_transfer_control.get_version()

  async def get_serial_number(self) -> str:
    return await self.file_transfer_control.get_serial_number()

  async def get_device_time(self) -> str:
    return await self.file_transfer_control.get_device_time()

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    return self.file_transfer_control.parse_run_log(text)

  async def run_with_ghost_touch(
    self,
    action: Callable[[_GhostTouchSession], GhostTouchResult],
  ) -> GhostTouchResult:
    await self.file_transfer_control.stop()
    try:
      return await asyncio.to_thread(self._run_with_ghost_touch_sync, action)
    finally:
      await self.file_transfer_control.setup()
      if self.file_transfer_control.port is not None:
        self.port = self.file_transfer_control.port

  def _run_with_ghost_touch_sync(
    self,
    action: Callable[[_GhostTouchSession], GhostTouchResult],
  ) -> GhostTouchResult:
    with self._open_ghost_touch() as ghost_touch:
      return action(ghost_touch)

  def _open_ghost_touch(self) -> _GhostTouchSession:
    if self.port is None:
      raise RuntimeError("Gemini X2 serial port is not resolved. Call setup() first.")
    session = self._ghost_touch_factory(port=self.port, **self._ghost_touch_kwargs)
    if not isinstance(session, _GhostTouchSession):
      session = cast(_GhostTouchSession, session)
    return session


class BTXGeminiX2ElectroporationBackend(ElectroporationBackend):
  """Prepared-run BTX Gemini X2 backend.

  The Gemini X2 uses two separate control paths on the same USB-connected device:
  `FileTransferControl` for Protocol Manager style file/protocol access, and `TheGhostTouch`
  for the RSI touchscreen workflow that arms and starts a user protocol.
  """

  UI_PROTOCOL_NAME_BYTES = FileTransferControl.UI_PROTOCOL_NAME_BYTES
  DEFAULT_TEMPORARY_PROTOCOL_PREFIX = "!PLR"
  PLATE_HANDLER_RESET_STATE_UNKNOWN = "unknown"
  PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED = "reset_confirmed"
  PLATE_HANDLER_RESET_STATE_CONTINUE_CURRENT_POSITION = "continue_current_position"
  PLATE_HANDLER_RESET_STATES = {
    PLATE_HANDLER_RESET_STATE_UNKNOWN,
    PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
    PLATE_HANDLER_RESET_STATE_CONTINUE_CURRENT_POSITION,
  }

  @dataclass(frozen=True)
  class PrepareRunParams(BackendParams):
    plate_handler_reset_state: str = "unknown"

  def __init__(
    self,
    driver: BTXGeminiX2Driver,
    *,
    plate_handler: Optional[BTXHT200] = None,
    temporary_protocol_prefix: str = DEFAULT_TEMPORARY_PROTOCOL_PREFIX,
  ) -> None:
    self.driver = driver
    self.plate_handler = plate_handler or BTXHT200()
    self._temporary_protocol_prefix = temporary_protocol_prefix
    self._is_setup = False

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    del backend_params
    try:
      # Setup only enforces ordering safety so a later process can resume an already prepared
      # `!PLR_...` run token without being blocked by the existing temp protocol.
      await self._ensure_temporary_protocol_prefix_order_safe(self._temporary_protocol_prefix)
      self._is_setup = True
    except Exception:
      self._is_setup = False
      raise

  async def _on_stop(self):
    self._is_setup = False

  def serialize(self) -> dict:
    return {
      "temporary_protocol_prefix": self._temporary_protocol_prefix,
      "plate_handler": {
        "device": self.plate_handler.__class__.__name__,
        "model": "HT-200",
        "assumed_pulse_count": self.plate_handler.assumed_pulse_count,
        "assumed_column_adjust": self.plate_handler.assumed_column_adjust,
      },
    }

  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> PreparedElectroporationRun:
    """Create a temporary protocol and leave the Gemini armed on ``Run Protocol``."""
    self._require_setup()
    if backend_params is None:
      backend_params = self.PrepareRunParams()
    if not isinstance(backend_params, self.PrepareRunParams):
      raise TypeError(
        "backend_params must be BTXGeminiX2ElectroporationBackend.PrepareRunParams or None."
      )
    resolved_prefix = self._temporary_protocol_prefix if prefix is None else prefix
    resolved_reset_state = self._resolve_plate_handler_reset_state(
      plate_columns=plate_columns,
      plate_handler_reset_state=backend_params.plate_handler_reset_state,
    )
    assumed_plate_handler_pulse_count, assumed_plate_handler_column_adjust = (
      self._resolve_plate_handler_manual_state(plate_columns=plate_columns)
    )
    # Preparing a new temp protocol is stricter than setup: earlier-sorting names and same-prefix
    # temp leftovers both make the "first user protocol" strategy unsafe.
    await self._ensure_temporary_protocol_prefix_available(resolved_prefix)

    # This snapshot lets start_prepared_run() identify the new log by diffing BTXDATA after GO.
    baseline_log_paths = tuple(await self.driver.list_log_files())
    protocol_name = self._make_temporary_protocol_name(resolved_prefix)
    add_result = await self.driver.add_protocol(
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
    except Exception:
      await self._cleanup_temporary_protocol(protocol_name, missing_ok=True)
      raise

    return PreparedElectroporationRun(
      protocol_name=protocol_name,
      protocol=protocol,
      plate_columns=plate_columns,
      prefix=resolved_prefix,
      prepared_at_utc=self._now_utc_iso(),
      baseline_log_paths=baseline_log_paths,
      prepare_result=ElectroporationPreparationDetails(
        prepared_state=_nested_state(_result_dict(rsi_result), "prepared_verification", "state"),
        protocol_setup=_result_dict(add_result),
        device_prepare={
          "plate_handler_reset_state": resolved_reset_state,
          "assumed_plate_handler_pulse_count": assumed_plate_handler_pulse_count,
          "assumed_plate_handler_column_adjust": assumed_plate_handler_column_adjust,
          **_result_dict(rsi_result),
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
    self._require_setup()
    prepared = self._coerce_prepared_run(prepared_run)

    started_at_utc = self._now_utc_iso()
    rsi_result = await self._run_with_ghost_touch(
      lambda ghost_touch: ghost_touch.start_prepared_user_protocol(
        protocol_name=prepared.protocol_name,
        home_after=home_after,
        max_run_seconds=max_run_seconds,
      )
    )

    try:
      log_capture = await self._collect_matching_new_log(
        before_logs=set(prepared.baseline_log_paths),
        protocol_name=prepared.protocol_name,
      )
    finally:
      cleanup = await self._cleanup_temporary_protocol(prepared.protocol_name, missing_ok=True)

    return ElectroporationRunResult(
      prepared_run=prepared,
      started_at_utc=started_at_utc,
      completed_at_utc=self._now_utc_iso(),
      rsi_result=ElectroporationExecutionDetails(
        verification_state=_nested_state(_result_dict(rsi_result), "verification", "state"),
        completed_state=_nested_state(_result_dict(rsi_result), "completed", "state"),
        final_state=(
          _nested_state(_result_dict(rsi_result), "home", "state")
          or _nested_state(_result_dict(rsi_result), "completed", "state")
        ),
        device_run=_result_dict(rsi_result),
      ),
      log_capture=ElectroporationLogCapture(
        matched_log_path=cast(Optional[str], _result_dict(log_capture).get("matched_log_path")),
        summary=dict(
          cast(Mapping[str, Any], _result_dict(log_capture).get("matched_log", {})).get(
            "summary", {}
          )
          if isinstance(_result_dict(log_capture).get("matched_log"), Mapping)
          else {}
        ),
        details=_result_dict(log_capture),
      ),
      cleanup=ElectroporationCleanup(
        deleted=cast(
          Optional[bool],
          cast(Mapping[str, Any], _result_dict(cleanup).get("delete_result", {})).get("deleted"),
        )
        if isinstance(_result_dict(cleanup).get("delete_result"), Mapping)
        else None,
        retry_used=bool(_result_dict(cleanup).get("delete_retry_used", False)),
        error=cast(Optional[str], _result_dict(cleanup).get("delete_error")),
        details=_result_dict(cleanup),
      ),
    )

  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
  ) -> ElectroporationCancellationResult:
    """Return the Gemini to a safe screen and delete the prepared temporary protocol."""
    self._require_setup()
    prepared = self._coerce_prepared_run(prepared_run)

    rsi_result = await self.driver.run_with_ghost_touch(
      lambda ghost_touch: ghost_touch.cancel_prepared_user_protocol(home_after=home_after)
    )
    cleanup = await self._cleanup_temporary_protocol(prepared.protocol_name, missing_ok=True)

    return ElectroporationCancellationResult(
      prepared_run=prepared,
      cancelled_at_utc=self._now_utc_iso(),
      rsi_result=ElectroporationCancellationDetails(
        final_state=_nested_state(_result_dict(rsi_result), "final_state", "state"),
        device_cancel=_result_dict(rsi_result),
      ),
      cleanup=ElectroporationCleanup(
        deleted=cast(
          Optional[bool],
          cast(Mapping[str, Any], _result_dict(cleanup).get("delete_result", {})).get("deleted"),
        )
        if isinstance(_result_dict(cleanup).get("delete_result"), Mapping)
        else None,
        retry_used=bool(_result_dict(cleanup).get("delete_retry_used", False)),
        error=cast(Optional[str], _result_dict(cleanup).get("delete_error")),
        details=_result_dict(cleanup),
      ),
    )

  async def get_device_info(self) -> Dict[str, Any]:
    """Return Gemini identity plus the supported electroporation workflow surface."""
    self._require_setup()
    version = await self.driver.get_version()
    serial_number = await self.driver.get_serial_number()
    device_time = await self.driver.get_device_time()
    protocols = await self.driver.list_protocols()
    plate_handler_info = self.plate_handler.get_device_info()
    return {
      "backend": self.__class__.__name__,
      "model": "Gemini X2",
      "port": self.driver.port,
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
      "plate_handler": plate_handler_info,
      "temporary_protocol_prefix": self._temporary_protocol_prefix,
      "protocol_transfer_control": "FileTransferControl",
      "touch_control": "TheGhostTouch",
    }

  async def list_protocols(self) -> list[str]:
    """List all user protocols visible through the PM serial interface."""
    self._require_setup()
    return await self.driver.list_protocols()

  async def get_protocol(self, protocol_name: str) -> Dict[str, Any]:
    """Fetch one stored user protocol by name."""
    self._require_setup()
    return await self.driver.get_protocol(protocol_name)

  async def add_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol,
    overwrite: bool = False,
  ) -> Dict[str, Any]:
    """Developer helper: write a user protocol directly through file transfer."""
    self._require_setup()
    return await self.driver.add_protocol(protocol_name, protocol, overwrite=overwrite)

  async def delete_protocol(self, protocol_name: str, missing_ok: bool = False) -> Dict[str, Any]:
    """Developer helper: delete a stored user protocol."""
    self._require_setup()
    return await self.driver.delete_protocol(protocol_name, missing_ok=missing_ok)

  async def list_log_files(self, root: str = "\\BTXDATA") -> list[str]:
    """Developer helper: enumerate run logs stored on the Gemini SD card."""
    self._require_setup()
    return await self.driver.list_log_files(root=root)

  async def fetch_sd_file(self, sd_path: str) -> str:
    """Developer helper: fetch one SD-card file from the Gemini."""
    self._require_setup()
    return await self.driver.fetch_sd_file(sd_path)

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    """Developer helper: parse a BTX run log into normalized fields."""
    return self.driver.parse_run_log(text)

  def _require_setup(self) -> None:
    if not self._is_setup:
      raise RuntimeError("Call setup() before using the Gemini X2 backend.")

  def _resolve_plate_handler_reset_state(
    self,
    *,
    plate_columns: Optional[int],
    plate_handler_reset_state: str,
  ) -> str:
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
      await self.driver.list_protocols(),
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
    protocols = await self.driver.list_protocols()
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

  def _temporary_protocol_preceding_conflicts(self, protocols: list[str], prefix: str) -> list[str]:
    reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
    anchor_key = reserved_anchor.casefold()
    conflicts = []
    for protocol_name in protocols:
      protocol_key = protocol_name.casefold()
      if protocol_key < anchor_key:
        conflicts.append(protocol_name)
    return sorted(conflicts, key=str.casefold)

  def _temporary_protocol_prefix_collisions(self, protocols: list[str], prefix: str) -> list[str]:
    reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
    anchor_key = reserved_anchor.casefold()
    collisions = []
    for protocol_name in protocols:
      if protocol_name.casefold().startswith(anchor_key):
        collisions.append(protocol_name)
    return sorted(collisions, key=str.casefold)

  def _coerce_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
  ) -> PreparedElectroporationRun:
    if isinstance(prepared_run, PreparedElectroporationRun):
      return prepared_run
    return PreparedElectroporationRun.from_dict(prepared_run)

  async def _run_with_ghost_touch(
    self,
    action: Callable[[_GhostTouchSession], GhostTouchResult],
  ) -> GhostTouchResult:
    self._require_setup()
    return await self.driver.run_with_ghost_touch(action)

  async def _force_home_via_ghost_touch(self) -> None:
    await self.driver.run_with_ghost_touch(lambda ghost_touch: ghost_touch.ensure_home())

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
      delete_result = await self.driver.delete_protocol(
        protocol_name,
        missing_ok=missing_ok,
      )
    except RuntimeError as exc:
      if "still exists after repeated delete attempts" not in str(exc):
        delete_error = str(exc)
      else:
        delete_retry_used = True
        try:
          await self._force_home_via_ghost_touch()
          delete_result = await self.driver.delete_protocol(
            protocol_name,
            missing_ok=missing_ok,
          )
        except Exception as retry_exc:  # pragma: no cover - hardware-specific recovery
          delete_error = str(retry_exc)
    except Exception as exc:  # pragma: no cover - hardware-specific recovery
      delete_error = str(exc)

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
    # Logs are matched by "new since prepare" plus protocol name, rather than by "latest log",
    # to avoid picking up unrelated historical runs.
    after_logs = set(await self.driver.list_log_files())
    new_logs = sorted(after_logs - before_logs)

    for log_path in new_logs:
      text = await self.driver.fetch_sd_file(log_path)
      parsed = self.driver.parse_run_log(text)
      if parsed["summary"]["protocol_name"] == protocol_name:
        return MatchedRunLogResult(
          before_count=len(before_logs),
          after_count=len(after_logs),
          new_log_paths=tuple(new_logs),
          matched_log_path=log_path,
          matched_log=parsed,
        )

    raise RuntimeError(
      f"No new BTXDATA log matched protocol '{protocol_name}'. New logs: {new_logs}"
    )

  def _make_temporary_protocol_name(self, prefix: str) -> str:
    reserved_anchor = self._temporary_protocol_sort_anchor(prefix)
    timestamp = datetime.now().strftime("%m%d%H%M%S")
    name = f"{reserved_anchor}{timestamp}"
    if len(name.encode("ascii")) > self.UI_PROTOCOL_NAME_BYTES:
      raise ValueError(
        f"Generated temp protocol name {name!r} exceeds the "
        f"{self.UI_PROTOCOL_NAME_BYTES}-byte Gemini UI limit. Shorten prefix={prefix!r}."
      )
    return name

  def _now_utc_iso(self) -> str:
    return datetime.now(timezone.utc).isoformat()


class BTXGeminiX2(Device):
  """BTX Gemini X2 electroporator."""

  def __init__(
    self,
    port: Optional[str] = None,
    *,
    file_transfer_control: Optional[FileTransferControl] = None,
    ghost_touch_factory: Optional[GhostTouchFactory] = None,
    ghost_touch_kwargs: Optional[dict[str, Any]] = None,
    plate_handler: Optional[BTXHT200] = None,
    temporary_protocol_prefix: str = BTXGeminiX2ElectroporationBackend.DEFAULT_TEMPORARY_PROTOCOL_PREFIX,
  ) -> None:
    driver = BTXGeminiX2Driver(
      port=port,
      file_transfer_control=file_transfer_control,
      ghost_touch_factory=ghost_touch_factory,
      ghost_touch_kwargs=ghost_touch_kwargs,
    )
    super().__init__(driver=driver)
    self.driver: BTXGeminiX2Driver = driver
    self.plate_handler = plate_handler or BTXHT200()
    self.electroporation = Electroporation(
      backend=BTXGeminiX2ElectroporationBackend(
        driver=driver,
        plate_handler=self.plate_handler,
        temporary_protocol_prefix=temporary_protocol_prefix,
      )
    )
    self._capabilities = [self.electroporation]
