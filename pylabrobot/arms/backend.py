import warnings

warnings.warn(
  "Importing from pylabrobot.arms.backend is deprecated. "
  "Use pylabrobot.capabilities.arms.backend instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms.backend import *  # noqa: F401,F403,E402
