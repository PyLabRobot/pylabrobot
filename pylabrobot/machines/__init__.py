import warnings

warnings.warn(
  "Importing from pylabrobot.machines is deprecated. Use pylabrobot.legacy.machines instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.machines import *  # noqa: F401,F403,E402
