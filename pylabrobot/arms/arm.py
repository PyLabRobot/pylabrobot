import warnings

warnings.warn(
  "Importing from pylabrobot.arms.arm is deprecated. Use pylabrobot.capabilities.arms.arm instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms.arm import *  # noqa: F401,F403,E402
