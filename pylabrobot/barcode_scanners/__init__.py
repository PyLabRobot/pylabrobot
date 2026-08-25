import warnings

warnings.warn(
  "Importing from pylabrobot.barcode_scanners is deprecated. "
  "Use pylabrobot.legacy.barcode_scanners instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.barcode_scanners import *  # noqa: F401,F403,E402
