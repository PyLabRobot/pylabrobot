from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Union

from pylabrobot.capabilities.capability import BackendParams

from .backend import ElectroporationBackend
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

logger = logging.getLogger(__name__)


class ElectroporationChatterboxBackend(ElectroporationBackend):
  """Chatterbox backend for device-free electroporation workflow tests."""

  DEFAULT_TEMPORARY_PROTOCOL_PREFIX = "!PLR"

  def __init__(
    self,
    temporary_protocol_prefix: str = DEFAULT_TEMPORARY_PROTOCOL_PREFIX,
  ) -> None:
    self.temporary_protocol_prefix = temporary_protocol_prefix
    self._counter = 0
    self.prepared_runs: Dict[str, PreparedElectroporationRun] = {}

  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> PreparedElectroporationRun:
    del backend_params
    resolved_prefix = self.temporary_protocol_prefix if prefix is None else prefix
    protocol_name = self._make_temporary_protocol_name(resolved_prefix)
    logger.info(
      "Preparing simulated electroporation protocol %s with plate_columns=%s.",
      protocol_name,
      plate_columns,
    )

    prepared = PreparedElectroporationRun(
      protocol_name=protocol_name,
      protocol=protocol,
      plate_columns=plate_columns,
      prefix=resolved_prefix,
      prepared_at_utc=self._now_utc_iso(),
      baseline_log_paths=(),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state="protocol_run_view",
        protocol_setup={
          "operation": "add_protocol",
          "protocol": protocol_name,
          "simulated": True,
          "exists_after": True,
        },
        device_prepare={
          "prepared_verification": {"state": "protocol_run_view"},
          "plate_columns": plate_columns,
          "simulated": True,
        },
      ),
    )
    self.prepared_runs[protocol_name] = prepared
    return prepared

  async def start_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> ElectroporationRunResult:
    prepared = self._coerce_prepared_run(prepared_run)
    logger.info(
      "Starting simulated electroporation protocol %s with max_run_seconds=%s.",
      prepared.protocol_name,
      max_run_seconds,
    )
    completed_state = "protocol_finish"
    final_state = "main_menu" if home_after else completed_state

    return ElectroporationRunResult(
      prepared_run=prepared,
      started_at_utc=self._now_utc_iso(),
      completed_at_utc=self._now_utc_iso(),
      rsi_result=ElectroporationExecutionDetails(
        verification_state="protocol_run_view",
        completed_state=completed_state,
        final_state=final_state,
        device_run={
          "verification": {"state": "protocol_run_view"},
          "after_start": {"state": "protocol_run_view"},
          "completed": {"state": completed_state},
          "home": None if not home_after else {"state": "main_menu"},
          "simulated": True,
        },
      ),
      log_capture=ElectroporationLogCapture(
        matched_log_path=None,
        summary={
          "protocol": prepared.protocol_name,
          "pulse_count": prepared.protocol.pulse_count,
          "simulated": True,
        },
        details={
          "before_count": 0,
          "after_count": 0,
          "new_log_paths": [],
          "matched_log_path": None,
          "matched_log": None,
          "simulated": True,
        },
      ),
      cleanup=self._cleanup_temporary_protocol(prepared.protocol_name),
    )

  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
  ) -> ElectroporationCancellationResult:
    prepared = self._coerce_prepared_run(prepared_run)
    final_state = "main_menu" if home_after else "protocol_run_view"
    logger.info("Cancelling simulated electroporation protocol %s.", prepared.protocol_name)

    return ElectroporationCancellationResult(
      prepared_run=prepared,
      cancelled_at_utc=self._now_utc_iso(),
      rsi_result=ElectroporationCancellationDetails(
        final_state=final_state,
        device_cancel={
          "cancelled": True,
          "final_state": {"state": final_state},
          "simulated": True,
        },
      ),
      cleanup=self._cleanup_temporary_protocol(prepared.protocol_name),
    )

  async def get_device_info(self) -> Dict[str, Any]:
    return {
      "backend": self.__class__.__name__,
      "model": "Chatterbox Electroporator",
      "supports_prepared_temporary_runs": True,
      "supports_serialized_prepared_runs": True,
      "supports_stored_protocol_runs": False,
      "supports_plate_columns": True,
      "temporary_protocol_prefix": self.temporary_protocol_prefix,
      "simulated": True,
    }

  def _make_temporary_protocol_name(self, prefix: str) -> str:
    self._counter += 1
    return f"{prefix}_{self._counter:06d}"

  def _coerce_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
  ) -> PreparedElectroporationRun:
    if isinstance(prepared_run, PreparedElectroporationRun):
      return prepared_run
    return PreparedElectroporationRun.from_dict(prepared_run)

  def _cleanup_temporary_protocol(self, protocol_name: str) -> ElectroporationCleanup:
    known_prepared_run = protocol_name in self.prepared_runs
    self.prepared_runs.pop(protocol_name, None)
    return ElectroporationCleanup(
      deleted=True,
      retry_used=False,
      error=None,
      details={
        "operation": "delete_protocol",
        "protocol": protocol_name,
        "known_prepared_run": known_prepared_run,
        "simulated": True,
      },
    )

  def _now_utc_iso(self) -> str:
    return datetime.now(timezone.utc).isoformat()
