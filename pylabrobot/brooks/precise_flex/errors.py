"""Deprecated: this module moved to ``pylabrobot.brooks.precise_flex.driver.errors``."""

import warnings

warnings.warn(
  "Importing from pylabrobot.brooks.precise_flex.errors is deprecated. "
  "Use pylabrobot.brooks.precise_flex.driver.errors instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.brooks.precise_flex.driver.errors import *  # noqa: E402, F401, F403
