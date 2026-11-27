"""The Mantis's liquid classes: one per viscosity band and chip type, in `mantis_mapping`."""

from .base import MantisLiquidClass
from .mapping import mantis_mapping

__all__ = ["MantisLiquidClass", "mantis_mapping"]
