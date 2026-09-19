"""Which supply bottle a driven syringe draws from."""

from __future__ import annotations

from typing import Literal

SyringeBottle = Literal["A1", "A2", "B1", "B2", "A1B1", "A1B2", "A2B1", "A2B2"]
"""Which supply bottle each driven syringe draws from.

Every syringe has two selectable bottles. Combined values name the pairing used when both syringes
run together: ``"A1B2"`` draws syringe A from its first bottle and syringe B from its second. A
step that names no bottle carries None rather than a member of this type.
"""

SYRINGE_BOTTLE_TO_BYTE: dict[SyringeBottle, int] = {
  "A1": 1,
  "A2": 2,
  "B1": 3,
  "B2": 4,
  "A1B1": 5,
  "A1B2": 6,
  "A2B1": 7,
  "A2B2": 8,
}
"""The value each selection is encoded as in a step command. No bottle encodes 0."""
