"""The Vantage's liquid classes, looked up by `get_vantage_liquid_class`. The classes themselves are
the module-level names of `mapping`."""

from .mapping import get_vantage_liquid_class, vantage_mapping

__all__ = ["get_vantage_liquid_class", "vantage_mapping"]
