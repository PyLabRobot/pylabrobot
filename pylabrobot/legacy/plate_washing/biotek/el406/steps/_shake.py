"""EL406 shake/soak step methods — legacy wrapper.

Implementation has moved to pylabrobot.agilent.biotek.el406.shaking_backend.
"""

from pylabrobot.agilent.biotek.el406.shaking_backend import (  # noqa: F401
  INTENSITY_TO_BYTE,
  Intensity,
  validate_intensity,
)
