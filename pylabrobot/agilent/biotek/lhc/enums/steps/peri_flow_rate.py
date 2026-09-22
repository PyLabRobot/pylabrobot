"""How fast a peristaltic pump runs."""

from __future__ import annotations

from typing import Literal

PeriFlowRate = Literal["Low", "Medium", "High"]
"""How fast a peristaltic step pumps."""

PERI_FLOW_RATE_TO_BYTE: dict[PeriFlowRate, int] = {"Low": 0, "Medium": 1, "High": 2}
"""The value each rate is encoded as in a step command."""
