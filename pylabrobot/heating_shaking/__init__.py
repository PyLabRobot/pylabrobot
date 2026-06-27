import warnings

warnings.warn(
  "Importing from pylabrobot.heating_shaking is deprecated. "
  "Use pylabrobot.legacy.heating_shaking instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.heating_shaking import *  # noqa: F401,F403,E402
