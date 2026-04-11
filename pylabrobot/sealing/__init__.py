import warnings

warnings.warn(
  "Importing from pylabrobot.sealing is deprecated. Use pylabrobot.legacy.sealing instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.sealing import *  # noqa: F401,F403,E402
