"""The shape of a Hamilton liquid class. Each instrument keeps its own tables of them, in its own
package: `pylabrobot.hamilton.star.liquid_classes`, `pylabrobot.hamilton.vantage.liquid_classes`."""

from .base import HamiltonLiquidClass

__all__ = ["HamiltonLiquidClass"]
