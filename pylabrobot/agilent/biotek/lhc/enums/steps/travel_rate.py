"""How fast an aspirate head travels down through a well."""

from __future__ import annotations

from typing import Literal

TravelRate = Literal[
  "1", "2", "3", "4", "5", "0 CW", "1 CW", "2 CW", "3 CW", "4 CW", "6 CW", "7 CW"
]
"""How fast the aspirate head travels down through a well.

Plain rates are two-speed: the tips descend at the listed rate and cover the last stretch at
1.0 mm/s (2.0 mm/s for ``"5"``), a slow final pass that pulls the well dry. Rates suffixed ``CW``
hold one speed the whole way down, leaving an adherent monolayer intact, and require the cell
washing module.

``"0 CW"`` and ``"7 CW"`` are available on the strip washer only. There is no ``"5 CW"``.
"""

TRAVEL_RATE_TO_BYTE: dict[TravelRate, int] = {
  "1": 1,
  "2": 2,
  "3": 3,
  "4": 4,
  "5": 5,
  "6 CW": 6,
  "1 CW": 7,
  "2 CW": 8,
  "3 CW": 9,
  "4 CW": 10,
  "0 CW": 11,
  "7 CW": 12,
}
"""The value each rate is encoded as in a step command."""

WASHER_TRAVEL_RATES: tuple[TravelRate, ...] = (
  "1",
  "2",
  "3",
  "4",
  "5",
  "1 CW",
  "2 CW",
  "3 CW",
  "4 CW",
  "6 CW",
)
"""The rates a wash manifold aspirate step accepts."""

STRIP_TRAVEL_RATES: tuple[TravelRate, ...] = WASHER_TRAVEL_RATES + ("0 CW", "7 CW")
"""The rates a strip washer aspirate step accepts."""
