import warnings

warnings.warn(
  "Importing from pylabrobot.arms.articulated_arm is deprecated. "
  "Use pylabrobot.capabilities.arms.articulated_arm instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms.articulated_arm import *  # noqa: F401,F403,E402
