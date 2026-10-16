"""Deprecated: this module moved to ``pylabrobot.brooks.precise_flex.driver.configuration``."""

import warnings

warnings.warn(
  "Importing from pylabrobot.brooks.precise_flex.config is deprecated. "
  "Use pylabrobot.brooks.precise_flex.driver.configuration instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.brooks.precise_flex.driver.configuration import (  # noqa: E402, F401
  Axis,
  PreciseFlexConfiguration,
)
