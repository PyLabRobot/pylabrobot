import warnings

warnings.warn(
  "Importing from pylabrobot.arms.standard is deprecated. "
  "Use pylabrobot.capabilities.arms.standard instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.capabilities.arms.standard import *  # noqa: F401,F403,E402
