import warnings

warnings.warn(
  "Importing from pylabrobot.arms.orientable_arm is deprecated. "
  "Use pylabrobot.capabilities.arms.orientable_arm instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms.orientable_arm import *  # noqa: F401,F403,E402
