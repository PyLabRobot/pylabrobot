import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling is deprecated. "
  "Use pylabrobot.legacy.liquid_handling instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling import *  # noqa: F401,F403,E402
