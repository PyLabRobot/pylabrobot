import warnings

warnings.warn(
  "Importing from pylabrobot.centrifuge is deprecated. Use pylabrobot.legacy.centrifuge instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.centrifuge import *  # noqa: F401,F403,E402
