from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pylabrobot.resources.barcode import Barcode


@dataclass
class RackScanEntry:
  """One decoded rack position."""

  position: str
  tube_id: Optional[str]
  status: Literal["OK", "NOREAD"]
  barcode: Optional[Barcode] = None


@dataclass
class RackScanResult:
  """A decoded rack scan."""

  rack_id: str
  entries: list[RackScanEntry]
  rack_barcode: Optional[Barcode] = None
