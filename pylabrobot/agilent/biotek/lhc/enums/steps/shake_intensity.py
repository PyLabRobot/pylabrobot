"""How vigorously the carrier shakes."""

from __future__ import annotations

from typing import Literal

ShakeIntensity = Literal["Variable", "Slow", "Medium", "Fast"]
"""How vigorously the carrier shakes.

The three fixed levels shake at a set frequency; ``"Variable"`` sweeps across the range instead of
holding one frequency.
"""

SHAKE_INTENSITY_TO_BYTE: dict[ShakeIntensity, int] = {
  "Variable": 1,
  "Slow": 2,
  "Medium": 3,
  "Fast": 4,
}
"""The value each intensity is encoded as in a step command."""
