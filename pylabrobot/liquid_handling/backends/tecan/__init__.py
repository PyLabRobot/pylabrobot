import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.backends.tecan is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.backends.tecan instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.backends.tecan import *  # noqa: F401,F403,E402
