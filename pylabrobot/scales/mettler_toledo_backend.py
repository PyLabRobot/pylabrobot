"""Backwards-compatible import shim. Use pylabrobot.scales.mettler_toledo instead."""

# TODO: remove after 2026-09
from pylabrobot.scales.mettler_toledo.backend import MettlerToledoWXS205SDUBackend  # noqa: F401
