import warnings

warnings.warn(
  "Importing from pylabrobot.microscopy is deprecated. Use pylabrobot.legacy.microscopes instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.microscopes import *  # noqa: F401,F403,E402
