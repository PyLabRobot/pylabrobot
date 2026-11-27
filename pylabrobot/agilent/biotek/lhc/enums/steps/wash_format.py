"""How much of a plate a wash covers."""

from __future__ import annotations

from typing import Literal

WashFormat = Literal["Plate", "Sector", "Strip"]
"""Which part of the plate a wash step covers per pass.

``"Plate"`` washes every well in one pass. ``"Sector"`` and ``"Strip"`` wash a subset at a time, so
a manifold with fewer tubes than the plate has wells can cover the whole plate in several passes.
"""

WASH_FORMAT_TO_BYTE: dict[WashFormat, int] = {"Plate": 0, "Sector": 1, "Strip": 2}
"""The value each format is encoded as in a step command."""
