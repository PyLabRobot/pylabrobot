import warnings

warnings.warn(
  "Importing from pylabrobot.storage is deprecated. Use pylabrobot.legacy.storage instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.storage import *  # noqa: F401,F403,E402
