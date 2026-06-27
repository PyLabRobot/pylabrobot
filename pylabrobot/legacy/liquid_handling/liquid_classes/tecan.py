"""Backwards-compatible re-export of the Tecan liquid classes.

The canonical definitions now live in :mod:`pylabrobot.tecan.evo.liquid_classes`.
This shim keeps the legacy import path working.
"""

from pylabrobot.tecan.evo.liquid_classes import (
  TecanLiquidClass,
  from_str,
  get_liquid_class,
  mapping,
)

__all__ = ["TecanLiquidClass", "from_str", "get_liquid_class", "mapping"]
