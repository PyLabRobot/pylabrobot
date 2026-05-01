import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.liquid_classes.tecan is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.liquid_classes.tecan instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.liquid_classes.tecan import *  # noqa: F401,F403,E402
