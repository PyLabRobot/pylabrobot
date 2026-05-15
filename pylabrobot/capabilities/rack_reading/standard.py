from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class RackScanEntry:
  """One decoded rack position."""

  position: str
  tube_id: Optional[str]
  status: Literal["OK", "NOREAD"]


@dataclass
class RackScanResult:
  """A decoded rack scan."""

  rack_id: str
  entries: list[RackScanEntry]
