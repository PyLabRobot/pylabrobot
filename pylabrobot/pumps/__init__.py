import warnings

warnings.warn(
  "Importing from pylabrobot.pumps is deprecated. Use pylabrobot.legacy.pumps instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.pumps import *  # noqa: E402
