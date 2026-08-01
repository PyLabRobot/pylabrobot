import warnings

warnings.warn(
  "Importing from pylabrobot.powder_dispensing is deprecated. "
  "Use pylabrobot.legacy.powder_dispensing instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.powder_dispensing import *  # noqa: F401,F403,E402
