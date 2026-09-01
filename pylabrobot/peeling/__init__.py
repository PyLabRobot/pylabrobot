import warnings

warnings.warn(
  "Importing from pylabrobot.peeling is deprecated. Use pylabrobot.legacy.peeling instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.peeling import *  # noqa: E402
