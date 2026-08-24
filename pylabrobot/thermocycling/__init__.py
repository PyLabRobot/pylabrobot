import warnings

warnings.warn(
  "Importing from pylabrobot.thermocycling is deprecated. "
  "Use pylabrobot.legacy.thermocycling instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.thermocycling import *  # noqa: F401,F403,E402
