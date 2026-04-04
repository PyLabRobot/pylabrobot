"""Data structures for the standard form of chip-based contactless liquid dispensing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
  from pylabrobot.resources import Well


@dataclass(frozen=True)
class DispenseOp:
  """A single dispense operation targeting a well.

  Attributes:
    resource: The target well to dispense into.
    volume: Volume to dispense in µL.
    chip: Chip number to use (1-6). If None, the backend selects automatically.
  """

  resource: Well
  volume: float
  chip: Optional[int] = None
