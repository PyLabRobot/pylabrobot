"""How a peristaltic cassette's tubes map onto wells."""

from __future__ import annotations

from typing import Literal

CassetteHead = Literal[
  "8 tubes to 8 wells",
  "8 tubes to 1 well",
  "8 tubes to 1 chute",
  "1 tube to 1 well",
]
"""How a peristaltic cassette's tubes map onto wells.

A chute head routes all tubes into a single waste chute and is used for purging rather than
dispensing. Only random-access dispense steps select a head; any other step carries None.
"""

CASSETTE_HEAD_TO_BYTE: dict[CassetteHead, int] = {
  "8 tubes to 8 wells": 0,
  "8 tubes to 1 well": 1,
  "8 tubes to 1 chute": 2,
  "1 tube to 1 well": 3,
}
"""The value each head is encoded as in a step command. No head encodes 255."""
