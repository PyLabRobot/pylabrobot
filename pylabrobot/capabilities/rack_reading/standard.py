from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class RackReaderError(Exception):
  """Base exception for rack reader operations."""


class RackReaderTimeoutError(RackReaderError):
  """Raised when a rack reader operation times out."""


class RackReaderState(enum.Enum):
  """Normalized rack reader states."""

  IDLE = "idle"
  SCANNING = "scanning"
  DATAREADY = "dataready"


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


@dataclass
class LayoutInfo:
  """One rack layout supported by the reader."""

  name: str
