import warnings

warnings.warn(
  "Importing from pylabrobot.scales is deprecated. Use pylabrobot.legacy.scales instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.scales import *  # noqa: F401,F403,E402
