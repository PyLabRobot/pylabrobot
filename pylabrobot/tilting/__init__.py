import warnings

warnings.warn(
  "Importing from pylabrobot.tilting is deprecated. Use pylabrobot.legacy.tilting instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.tilting import *  # noqa: E402
