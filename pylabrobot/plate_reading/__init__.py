import warnings

warnings.warn(
  "Importing from pylabrobot.plate_reading is deprecated. "
  "Use pylabrobot.legacy.plate_reading instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.plate_reading import *  # noqa: F401,F403,E402
