"""What to do when the fitted cassette is not the one a step needs."""

from __future__ import annotations

from typing import Literal

CassetteMode = Literal["Error", "Prompt", "Auto set"]
"""What to do when the fitted cassette is not the one a step requires.

``"Error"`` aborts the run, ``"Prompt"`` asks for the correct cassette to be fitted, and
``"Auto set"`` accepts the fitted cassette and adjusts the step to it.
"""

CASSETTE_MODE_TO_BYTE: dict[CassetteMode, int] = {"Error": 0, "Prompt": 1, "Auto set": 2}
"""The value each mode is encoded as. An unset mode encodes 255."""
