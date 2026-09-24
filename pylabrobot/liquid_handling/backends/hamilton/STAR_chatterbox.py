import warnings

warnings.warn(
  "Importing from pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox is deprecated. "
  "Use pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox instead.",
  DeprecationWarning,
  stacklevel=2,
)

from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox import *  # noqa: F403, E402
