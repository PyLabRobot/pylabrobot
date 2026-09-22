"""Which peristaltic pump a step drives."""

from __future__ import annotations

from typing import Literal

PeriPump = Literal["Primary", "Secondary"]
"""Which peristaltic pump a step drives.

A second pump is an option; a step naming ``"Secondary"`` fails on an instrument without one. A
step that leaves the choice to the instrument carries None.
"""

PERI_PUMP_TO_BYTE: dict[PeriPump, int] = {"Primary": 1, "Secondary": 2}
"""The value each pump is encoded as in a step command. An unspecified pump encodes 0."""
