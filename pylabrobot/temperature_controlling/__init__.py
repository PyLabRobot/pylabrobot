import warnings

warnings.warn(
  "Importing from pylabrobot.temperature_controlling is deprecated. "
  "Use pylabrobot.legacy.temperature_controlling instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.temperature_controlling import *  # noqa: F401,F403,E402
