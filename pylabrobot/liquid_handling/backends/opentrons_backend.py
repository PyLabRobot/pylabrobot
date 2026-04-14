import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.backends.opentrons_backend is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.backends.opentrons_backend instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import *  # noqa: F401,F403,E402
