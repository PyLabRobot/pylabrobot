import warnings

warnings.warn(
  "Importing from pylabrobot.only_fans is deprecated. Use pylabrobot.legacy.only_fans instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.only_fans import *  # noqa: F401,F403,E402
