"""The STAR's liquid classes: one per tip, liquid and dispense mode, looked up by
`get_star_liquid_class`. The classes themselves are the module-level names of `mapping`."""

from .mapping import get_star_liquid_class, star_mapping

__all__ = ["get_star_liquid_class", "star_mapping"]
