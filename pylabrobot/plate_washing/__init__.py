import warnings

warnings.warn(
  "Importing from pylabrobot.plate_washing is deprecated. "
  "Use pylabrobot.legacy.plate_washing instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.plate_washing import *  # noqa: F401,F403,E402
