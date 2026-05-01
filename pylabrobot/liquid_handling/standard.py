import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.standard is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.standard instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.standard import *  # noqa: F401,F403,E402
