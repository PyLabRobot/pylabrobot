from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RackScanEntry:
  """One decoded rack position."""

  position: str
  tube_id: Optional[str]
  status: str
  free_text: str = ""


@dataclass
class RackScanResult:
  """A decoded rack scan."""

  rack_id: str
  date: str
  time: str
  entries: list[RackScanEntry]
