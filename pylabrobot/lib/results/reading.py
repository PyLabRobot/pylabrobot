"""Reading a results directory, also while its writer appends to it."""

import json
import os
from typing import Any, Dict, List, Union

from .directory import ROWS


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
