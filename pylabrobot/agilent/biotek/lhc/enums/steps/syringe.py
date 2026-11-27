"""Which syringe a step drives."""

from __future__ import annotations

from typing import Literal

Syringe = Literal["A", "B", "Both"]
"""Which syringe a syringe step drives.

``"Both"`` dispenses from A and B simultaneously and requires a double syringe box. A step that
drives no syringe carries None rather than a member of this type.
"""

SYRINGE_TO_BYTE: dict[Syringe, int] = {"A": 1, "B": 2, "Both": 3}
"""The value each selection is encoded as in a step command. No syringe encodes 0."""
