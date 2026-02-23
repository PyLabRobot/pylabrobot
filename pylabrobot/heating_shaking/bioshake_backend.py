import warnings

from pylabrobot.shaking.bioshake_backend import BioShake as _BioShake


class BioShake(_BioShake):
  """Deprecated import path for BioShake.

  BioShake is a shaker-only backend and lives in ``pylabrobot.shaking``.
  """

  def __init__(self, *args, **kwargs):
    warnings.warn(
      "pylabrobot.heating_shaking.bioshake_backend.BioShake is deprecated. "
      "Use pylabrobot.shaking.bioshake_backend.BioShake instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    super().__init__(*args, **kwargs)
