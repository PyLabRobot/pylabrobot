import warnings

from .agilent.vspin_backend import (  # noqa: F401
  Access2Backend,
  VSpinBackend,
)

warnings.warn(
  "pylabrobot.centrifuge.vspin_backend is deprecated and will be removed in a future release. "
  "Please use pylabrobot.centrifuge.agilent.vspin_backend instead.",
)
