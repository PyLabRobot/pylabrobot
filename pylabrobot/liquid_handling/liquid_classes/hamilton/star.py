import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.liquid_classes.hamilton.star is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.liquid_classes.hamilton.star instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.liquid_classes.hamilton.star import *  # noqa: F401,F403,E402
