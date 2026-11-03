"""Plate-reader results as one tidy table: one row per measured value, every setting a column.

Rows are plain dicts with the keys of `COLUMNS`, in that order; a column that does not apply to a
row is None. Timestamps are timezone-aware, in UTC. Each result produces its own rows (`get_rows`).
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

__all__ = ["COLUMNS", "get_table"]

COLUMNS = (
  "read",
  "mode",
  "start_timestamp",
  "end_timestamp",
  "run_start_timestamp",
  "cycle",
  "due_timestamp",
  "well",
  "row",
  "column",
  "wavelength",
  "excitation_wavelength",
  "excitation_bandwidth",
  "emission_wavelength",
  "emission_bandwidth",
  "focal_height",
  "optic_position",
  "gain",
  "temperature",
  "quantity",
  "value",
)

_IDENTIFIER = re.compile(r"([A-Z]+)([0-9]+)")


def _get_well_columns(identifier: str) -> Dict[str, Any]:
  """The well, row and column of a well identifier such as 'B3'.

  Raises:
    ValueError: if `identifier` is not a row label and a column number.
  """
  match = _IDENTIFIER.fullmatch(identifier)
  if match is None:
    raise ValueError(f"{identifier!r} is not a well identifier such as 'B3'")
  return {"well": identifier, "row": match.group(1), "column": int(match.group(2))}


def get_base_row(
  read: int, mode: str, start_timestamp: datetime, end_timestamp: datetime, **columns: Any
) -> Dict[str, Any]:
  """A row of every column: None but `read`, `mode`, the timestamps in UTC and `columns`."""
  base: Dict[str, Any] = dict.fromkeys(COLUMNS)
  base.update(
    read=read,
    mode=mode,
    start_timestamp=start_timestamp.astimezone(timezone.utc),
    end_timestamp=end_timestamp.astimezone(timezone.utc),
    **columns,
  )
  return base


def get_well_rows(base: Dict[str, Any], data: Dict[str, float]) -> List[Dict[str, Any]]:
  """One row per well of `data`: a copy of `base` with the well and its value."""
  return [
    {**base, **_get_well_columns(identifier), "value": value} for identifier, value in data.items()
  ]


def get_spectrum_rows(
  base: Dict[str, Any], data: Dict[str, Dict[int, float]], wavelength_column: str
) -> List[Dict[str, Any]]:
  """One row per well and wavelength of `data`, the wavelength in `wavelength_column`."""
  return [
    {**base, **_get_well_columns(identifier), wavelength_column: wavelength, "value": value}
    for identifier, spectrum in data.items()
    for wavelength, value in spectrum.items()
  ]


def get_table(results: Sequence[Any], *, first_read: int = 0) -> List[Dict[str, Any]]:
  """The rows of `results`: each result's own (`get_rows`), one result one read.

  Args:
    results: absorbance, fluorescence and luminescence results and spectra.
    first_read: the `read` of the first result; each next result counts up by one.

  Raises:
    ValueError: if a well is not a well identifier.
  """
  return [row for read, result in enumerate(results, first_read) for row in result.get_rows(read)]
