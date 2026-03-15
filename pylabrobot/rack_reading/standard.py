from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional


class RackReaderError(Exception):
  """Base exception for rack reader operations."""


class RackReaderTimeoutError(RackReaderError):
  """Raised when a rack reader operation times out."""


class RackReaderState(enum.Enum):
  IDLE = "idle"
  SCANNING = "scanning"
  DATAREADY = "dataready"


@dataclass
class RackScanEntry:
  position: str
  tube_id: Optional[str]
  status: str
  free_text: str = ""


@dataclass
class RackScanResult:
  rack_id: str
  date: str
  time: str
  entries: List[RackScanEntry]


@dataclass
class LayoutInfo:
  name: str
