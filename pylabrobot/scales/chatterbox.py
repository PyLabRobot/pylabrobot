"""Backwards-compatible import shim. Use pylabrobot.scales.simulator instead."""

# TODO: remove in v1
from pylabrobot.scales.simulator import ScaleSimulator as ScaleChatterboxBackend  # noqa: F401
