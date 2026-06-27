import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.backends.hamilton is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.backends.hamilton instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.backends.hamilton import *  # noqa: F401,F403,E402
