"""Which peristaltic cassette a step requires."""

from __future__ import annotations

from typing import Literal

CassetteType = Literal[
  "Any",
  "1uL",
  "5uL",
  "10uL",
  "PeriWash aspirate",
  "PeriWash dispense",
  "Any random access",
  "Any non-random access",
]
"""The peristaltic pump cassette a step requires.

Tubing cassettes are named by their per-revolution delivery volume. ``"Any"`` accepts whatever is
fitted; ``"Any random access"`` and ``"Any non-random access"`` narrow that to the cassettes with
and without a random-access dispense head. The PeriWash cassettes are set on the instrument rather
than requested by a step. A step with no requirement carries None rather than a member of this
type.
"""

CASSETTE_TYPE_TO_BYTE: dict[CassetteType, int] = {
  "Any": 0,
  "1uL": 1,
  "5uL": 2,
  "10uL": 3,
  "PeriWash aspirate": 4,
  "PeriWash dispense": 5,
  "Any random access": 253,
  "Any non-random access": 254,
}
"""The value each cassette is encoded as in a step command. No requirement encodes 255."""
