import warnings

warnings.warn(
  "Importing from pylabrobot.arms is deprecated. Use pylabrobot.capabilities.arms instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms import *  # noqa: F401,F403,E402
