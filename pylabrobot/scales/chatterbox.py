"""Backwards-compatible import shim. Use pylabrobot.scales.simulator instead."""

# TODO: remove after 2026-09
from pylabrobot.scales.simulator import ScaleSimulator as ScaleChatterboxBackend  # noqa: F401
