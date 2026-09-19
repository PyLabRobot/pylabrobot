"""Which axis the carrier shakes along."""

from __future__ import annotations

from typing import Literal

ShakeAxis = Literal["X", "Y"]
"""The axis the carrier shakes along."""

SHAKE_AXIS_TO_BYTE: dict[ShakeAxis, int] = {"X": 0, "Y": 1}
"""The value each axis is encoded as in a step command."""
