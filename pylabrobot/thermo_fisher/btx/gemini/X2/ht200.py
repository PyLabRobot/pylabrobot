from __future__ import annotations

from typing import Any, Dict, Optional


class BTXHT200:
  """Manual-state model for the BTX HT-200 plate handler.

  The HT-200 has no separate software control path here. Column handling is driven through the
  Gemini X2 UI, so this object owns only the caller's assumed manual handler state.
  """

  def __init__(
    self,
    *,
    assumed_pulse_count: Optional[int] = None,
    assumed_column_adjust: Optional[int] = None,
  ) -> None:
    self._assumed_pulse_count = self._coerce_assumed_pulse_count(assumed_pulse_count)
    self._assumed_column_adjust = self._coerce_assumed_column_adjust(assumed_column_adjust)

  @property
  def assumed_pulse_count(self) -> Optional[int]:
    return self._assumed_pulse_count

  @property
  def assumed_column_adjust(self) -> Optional[int]:
    return self._assumed_column_adjust

  def configure_manual_state(
    self,
    *,
    pulse_count: Optional[int] = None,
    column_adjust: Optional[int] = None,
  ) -> None:
    """Record the caller's current HT-200 manual configuration assumptions."""
    self._assumed_pulse_count = self._coerce_assumed_pulse_count(pulse_count)
    self._assumed_column_adjust = self._coerce_assumed_column_adjust(column_adjust)

  def clear_manual_state(self) -> None:
    """Forget the current HT-200 manual configuration assumptions."""
    self._assumed_pulse_count = None
    self._assumed_column_adjust = None

  def require_manual_state(self) -> tuple[int, int]:
    """Return the configured manual assumptions needed for a Gemini plate-handler run."""
    pulse_count = self._assumed_pulse_count
    column_adjust = self._assumed_column_adjust
    missing = []
    if pulse_count is None:
      missing.append("assumed_pulse_count")
    if column_adjust is None:
      missing.append("assumed_column_adjust")
    if missing:
      raise ValueError(
        "HT-200 manual state is not fully configured. Missing: "
        f"{', '.join(missing)}. Configure the HT-200 before preparing a run "
        "that uses plate_columns."
      )
    assert pulse_count is not None
    assert column_adjust is not None
    return pulse_count, column_adjust

  def get_device_info(self) -> Dict[str, Any]:
    return {
      "device": self.__class__.__name__,
      "model": "HT-200",
      "access_control_mode": "manual",
      "manual_access_effect": "lid_cycle_resets_column_start_to_1",
      "assumed_pulse_count": self._assumed_pulse_count,
      "assumed_column_adjust": self._assumed_column_adjust,
    }

  def _coerce_assumed_pulse_count(self, value: Optional[int]) -> Optional[int]:
    if value is None:
      return None
    pulse_count = int(value)
    if pulse_count <= 0:
      raise ValueError("assumed_pulse_count must be a positive integer or None.")
    return pulse_count

  def _coerce_assumed_column_adjust(self, value: Optional[int]) -> Optional[int]:
    if value is None:
      return None
    return int(value)
