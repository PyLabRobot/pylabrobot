"""Which buffer inlet a step draws from."""

from __future__ import annotations

from typing import Literal

Buffer = Literal["A", "B", "C", "D"]
"""Which buffer inlet a dispensing step draws from.

Only instruments with a valve box fitted can select anything other than ``"A"``.
"""

BUFFERS: tuple[Buffer, ...] = ("A", "B", "C", "D")
"""Every buffer inlet, for validation and iteration."""
