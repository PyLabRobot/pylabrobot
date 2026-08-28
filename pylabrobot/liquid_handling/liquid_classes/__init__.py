import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.liquid_classes is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.liquid_classes instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.liquid_classes import *  # noqa: E402
