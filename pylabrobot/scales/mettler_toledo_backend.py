"""Backwards-compatible import shim. Use pylabrobot.scales.mettler_toledo instead."""

# TODO: remove in v1
from pylabrobot.scales.mettler_toledo.backend import MettlerToledoWXS205SDUBackend  # noqa: F401
