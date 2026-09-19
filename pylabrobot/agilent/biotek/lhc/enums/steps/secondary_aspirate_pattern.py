"""The path a second aspirate traces in the well."""

from __future__ import annotations

from typing import Literal

SecondaryAspiratePattern = Literal["None", "Point", "Circle", "Square"]
"""The path a secondary aspirate traces in the well after the primary aspirate.

``"Point"`` aspirates once at the offset position; ``"Circle"`` and ``"Square"`` sweep the tip
around the well bottom to reach residue the primary aspirate leaves behind.
"""

SECONDARY_ASPIRATE_PATTERN_TO_BYTE: dict[SecondaryAspiratePattern, int] = {
  "None": 0,
  "Point": 1,
  "Circle": 2,
  "Square": 3,
}
"""The value each pattern is encoded as in a step command."""
