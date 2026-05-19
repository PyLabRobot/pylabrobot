import warnings

from .agilent.access2 import Access2  # noqa: F401

warnings.warn(
  "pylabrobot.centrifuge.access2 is deprecated and will be removed in a future release. "
  "Please use pylabrobot.centrifuge.agilent.access2 instead.",
)
