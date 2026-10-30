"""Reading a results directory, also while its writer appends to it."""

import json
import math
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union

from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.lib.luminescence.results import LuminescenceResult

from .directory import ROWS

ReadResult = Union[
  AbsorbanceResult,
  AbsorbanceSpectrumResult,
  FluorescenceResult,
  FluorescenceSpectrumResult,
  LuminescenceResult,
]

# The modes `get_results` rebuilds, each with the quantity its rows hold.
_QUANTITIES = {
  "absorbance": "optical_density",
  "absorbance_spectrum": "optical_density",
  "fluorescence": "counts",
  "fluorescence_spectrum": "counts",
  "luminescence": "counts",
}


def load_rows(directory: Union[str, os.PathLike]) -> List[Dict[str, Any]]:
  """Every complete row of a results directory's `rows.jsonl`.

  A cut last line (one without its newline, being written or left by a crash) is skipped. A missing
  file gives no rows.

  Raises:
    ValueError: if a complete line is not a JSON object; the message names its line number.
  """
  try:
    with open(os.path.join(directory, ROWS), "rb") as file:
      data = file.read()
  except FileNotFoundError:
    return []
  rows = []
  for number, line in enumerate(data.split(b"\n")[:-1], start=1):
    try:
      row = json.loads(line)
    except ValueError as error:
      raise ValueError(f"{ROWS} line {number} is not valid JSON: {error}") from None
    if not isinstance(row, dict):
      raise ValueError(f"{ROWS} line {number} is not a JSON object")
    rows.append(row)
  return rows


def _get_timestamp(timestamp: Union[None, str, datetime]) -> Optional[datetime]:
  """A row's timestamp as a datetime: ISO 8601 text parsed, a datetime as it is; None for None."""
  return datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else timestamp


def _get_value(value: Optional[float]) -> float:
  """A row's value as a float; NaN for a value written as null (not a finite number)."""
  return math.nan if value is None else float(value)


def _get_result(read: int, rows: List[Dict[str, Any]]) -> ReadResult:
  """The result of read `read` from its rows, as the shared result type of its mode.

  Raises:
    ValueError: if the read's mode or quantity has no shared result type.
  """
  first = rows[0]
  mode = first["mode"]
  if mode not in _QUANTITIES:
    raise ValueError(f"read {read} is a {mode} read, which has no shared result type to rebuild")
  for row in rows:
    if row["mode"] != mode or row["quantity"] != _QUANTITIES[mode]:
      raise ValueError(
        f"read {read} holds {row['quantity']} ({row['mode']}): only {_QUANTITIES[mode]} rebuilds"
      )
  shared: Dict[str, Any] = {
    "temperature": first["temperature"],
    "start_timestamp": _get_timestamp(first["start_timestamp"]),
    "end_timestamp": _get_timestamp(first["end_timestamp"]),
    "current_cycle": {
      "run_start_timestamp": _get_timestamp(first["run_start_timestamp"]),
      "cycle": first["cycle"],
      "due_timestamp": _get_timestamp(first["due_timestamp"]),
    },
  }
  if mode != "absorbance" and mode != "absorbance_spectrum":
    shared.update(emission_wavelength=first["emission_wavelength"])
    shared.update(emission_bandwidth=first["emission_bandwidth"])
  if mode == "fluorescence" or mode == "fluorescence_spectrum":
    shared.update(excitation_wavelength=first["excitation_wavelength"])
    shared.update(excitation_bandwidth=first["excitation_bandwidth"])
  if mode == "absorbance":
    shared.update(wavelength=first["wavelength"])
  if mode.endswith("_spectrum"):
    column = "wavelength"
    if mode == "fluorescence_spectrum":  # the scanned wavelength is the one that changes
      column = "excitation_wavelength"
      if len({row[column] for row in rows}) == 1:
        column = "emission_wavelength"
      shared[column] = None
    spectra: Dict[str, Dict[int, float]] = {}
    for row in rows:
      spectra.setdefault(row["well"], {})[int(row[column])] = _get_value(row["value"])
    if mode == "absorbance_spectrum":
      return AbsorbanceSpectrumResult(data=spectra, **shared)
    return FluorescenceSpectrumResult(data=spectra, **shared)
  data = {row["well"]: _get_value(row["value"]) for row in rows}
  if mode == "absorbance":
    return AbsorbanceResult(data=data, **shared)
  if mode == "fluorescence":
    return FluorescenceResult(data=data, **shared)
  return LuminescenceResult(data=data, **shared)


def get_results(rows: Iterable[Dict[str, Any]]) -> List[ReadResult]:
  """The results `rows` are the rows of, one per read, in read order: the reverse of `get_table`.

  Each read comes back as the shared result type of its mode, with its `current_cycle`. Settings
  only one reader has (a gain, a focal height) are not in the shared types and are left out;
  timestamps come back in UTC, and a value written as null as NaN.

  Args:
    rows: rows of the tidy table, timestamps as ISO 8601 text (as `load_rows` gives them) or as
      datetimes (as `get_table` does).

  Raises:
    ValueError: if a read has no shared result type (raw counts); the message names the read.
  """
  reads: Dict[int, List[Dict[str, Any]]] = {}
  for row in rows:
    reads.setdefault(row["read"], []).append(row)
  return [_get_result(read, reads[read]) for read in sorted(reads)]
