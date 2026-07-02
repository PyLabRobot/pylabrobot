from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class ElectroporationProtocol:
  """Portable protocol definition for electroporation runs.

  Exactly one waveform-specific parameter set must be present:
  - `square`: `duration_us`
  - `exponential`: `resistance_ohms` and `capacitance_uf`
  """

  protocol_type: str
  pulse_amplitude_volts: int
  gap_mm: float
  pulse_count: int = 1
  pulse_interval_seconds: Optional[float] = None
  duration_us: Optional[int] = None
  resistance_ohms: Optional[int] = None
  capacitance_uf: Optional[int] = None

  def as_parameters(self) -> Dict[str, Any]:
    return {
      "protocol_type": self.protocol_type,
      "pulse_amplitude_volts": self.pulse_amplitude_volts,
      "gap_mm": self.gap_mm,
      "pulse_count": self.pulse_count,
      "pulse_interval_seconds": self.pulse_interval_seconds,
      "duration_us": self.duration_us,
      "resistance_ohms": self.resistance_ohms,
      "capacitance_uf": self.capacitance_uf,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationProtocol":
    return cls(
      protocol_type=str(data["protocol_type"]),
      pulse_amplitude_volts=int(data["pulse_amplitude_volts"]),
      gap_mm=float(data["gap_mm"]),
      pulse_count=int(data.get("pulse_count", 1)),
      pulse_interval_seconds=(
        None
        if data.get("pulse_interval_seconds") is None
        else float(data["pulse_interval_seconds"])
      ),
      duration_us=None if data.get("duration_us") is None else int(data["duration_us"]),
      resistance_ohms=(
        None if data.get("resistance_ohms") is None else int(data["resistance_ohms"])
      ),
      capacitance_uf=None if data.get("capacitance_uf") is None else int(data["capacitance_uf"]),
    )


@dataclass(frozen=True)
class ElectroporationPreparationDetails:
  """Generic preparation details for a prepared electroporation run."""

  prepared_state: Optional[str]
  protocol_setup: Dict[str, Any]
  device_prepare: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "prepared_state": self.prepared_state,
      "protocol_setup": self.protocol_setup,
      "device_prepare": self.device_prepare,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationPreparationDetails":
    return cls(
      prepared_state=None if data["prepared_state"] is None else str(data["prepared_state"]),
      protocol_setup=dict(data["protocol_setup"]),
      device_prepare=dict(data["device_prepare"]),
    )


@dataclass(frozen=True)
class ElectroporationExecutionDetails:
  """Generic device-run details for a started electroporation run."""

  verification_state: Optional[str]
  completed_state: Optional[str]
  final_state: Optional[str]
  device_run: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "verification_state": self.verification_state,
      "completed_state": self.completed_state,
      "final_state": self.final_state,
      "device_run": self.device_run,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationExecutionDetails":
    return cls(
      verification_state=(
        None if data["verification_state"] is None else str(data["verification_state"])
      ),
      completed_state=None if data["completed_state"] is None else str(data["completed_state"]),
      final_state=None if data["final_state"] is None else str(data["final_state"]),
      device_run=dict(data["device_run"]),
    )


@dataclass(frozen=True)
class ElectroporationCancellationDetails:
  """Generic device-cancel details for a prepared electroporation run."""

  final_state: Optional[str]
  device_cancel: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "final_state": self.final_state,
      "device_cancel": self.device_cancel,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationCancellationDetails":
    return cls(
      final_state=None if data["final_state"] is None else str(data["final_state"]),
      device_cancel=dict(data["device_cancel"]),
    )


@dataclass(frozen=True)
class ElectroporationLogCapture:
  """Generic log-capture result for an electroporation run."""

  matched_log_path: Optional[str]
  summary: Dict[str, Any]
  details: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "matched_log_path": self.matched_log_path,
      "summary": self.summary,
      "details": self.details,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationLogCapture":
    return cls(
      matched_log_path=None if data["matched_log_path"] is None else str(data["matched_log_path"]),
      summary=dict(data["summary"]),
      details=dict(data["details"]),
    )


@dataclass(frozen=True)
class ElectroporationCleanup:
  """Generic cleanup result after a prepared or completed electroporation run."""

  deleted: Optional[bool]
  retry_used: bool
  error: Optional[str]
  details: Dict[str, Any]

  def as_dict(self) -> Dict[str, Any]:
    return {
      "deleted": self.deleted,
      "retry_used": self.retry_used,
      "error": self.error,
      "details": self.details,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "ElectroporationCleanup":
    return cls(
      deleted=None if data["deleted"] is None else bool(data["deleted"]),
      retry_used=bool(data["retry_used"]),
      error=None if data["error"] is None else str(data["error"]),
      details=dict(data["details"]),
    )


@dataclass(frozen=True)
class PreparedElectroporationRun:
  """Prepared temporary run left armed on the device run screen.

  Serialize with `as_dict()` and restore with `from_dict()` in a later process.
  """

  protocol_name: str
  protocol: ElectroporationProtocol
  plate_columns: Optional[int]
  prefix: str
  prepared_at_utc: str
  baseline_log_paths: tuple[str, ...]
  prepare_result: ElectroporationPreparationDetails

  def as_dict(self) -> Dict[str, Any]:
    return {
      "protocol_name": self.protocol_name,
      "protocol": self.protocol.as_parameters(),
      "plate_columns": self.plate_columns,
      "prefix": self.prefix,
      "prepared_at_utc": self.prepared_at_utc,
      "baseline_log_paths": list(self.baseline_log_paths),
      "prepare_result": self.prepare_result.as_dict(),
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "PreparedElectroporationRun":
    return cls(
      protocol_name=str(data["protocol_name"]),
      protocol=ElectroporationProtocol.from_dict(data["protocol"]),
      plate_columns=None if data["plate_columns"] is None else int(data["plate_columns"]),
      prefix=str(data["prefix"]),
      prepared_at_utc=str(data["prepared_at_utc"]),
      baseline_log_paths=tuple(str(path) for path in data["baseline_log_paths"]),
      prepare_result=ElectroporationPreparationDetails.from_dict(data["prepare_result"]),
    )


@dataclass(frozen=True)
class ElectroporationRunResult:
  """Result of starting a previously prepared electroporation run."""

  prepared_run: PreparedElectroporationRun
  started_at_utc: str
  completed_at_utc: str
  rsi_result: ElectroporationExecutionDetails
  log_capture: ElectroporationLogCapture
  cleanup: ElectroporationCleanup

  def as_dict(self) -> Dict[str, Any]:
    return {
      "prepared_run": self.prepared_run.as_dict(),
      "started_at_utc": self.started_at_utc,
      "completed_at_utc": self.completed_at_utc,
      "rsi_result": self.rsi_result.as_dict(),
      "log_capture": self.log_capture.as_dict(),
      "cleanup": self.cleanup.as_dict(),
    }


@dataclass(frozen=True)
class ElectroporationCancellationResult:
  """Result of cancelling a prepared temporary electroporation run."""

  prepared_run: PreparedElectroporationRun
  cancelled_at_utc: str
  rsi_result: ElectroporationCancellationDetails
  cleanup: ElectroporationCleanup

  def as_dict(self) -> Dict[str, Any]:
    return {
      "prepared_run": self.prepared_run.as_dict(),
      "cancelled_at_utc": self.cancelled_at_utc,
      "rsi_result": self.rsi_result.as_dict(),
      "cleanup": self.cleanup.as_dict(),
    }
