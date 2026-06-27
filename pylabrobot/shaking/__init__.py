import warnings

warnings.warn(
  "Importing from pylabrobot.shaking is deprecated. Use pylabrobot.legacy.shaking instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.shaking import *  # noqa: F401,F403,E402
