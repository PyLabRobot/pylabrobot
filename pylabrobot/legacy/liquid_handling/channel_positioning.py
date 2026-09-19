"""Deprecated: moved to `pylabrobot.utils.liquid_handling.channel_positioning`.

Where a device's channels go inside a container is shared by every
multi-channel pipette device, not the legacy liquid handler's alone.
Importing from here still works and returns the very same objects, but warns.
"""

# TODO: Remove >2026-12

import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the names this module forwards, for type checkers and IDEs
  from pylabrobot.utils.liquid_handling.channel_positioning import (  # noqa: F401
    GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
    MIN_SPACING_BETWEEN_CHANNELS,
    MIN_SPACING_EDGE,
    compute_channel_offsets,
    compute_nonconsecutive_channel_offsets,
    get_tight_single_resource_liquid_op_offsets,
    get_wide_single_resource_liquid_op_offsets,
    required_spacing_between,
  )

_MOVED = (
  "GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS",
  "MIN_SPACING_BETWEEN_CHANNELS",
  "MIN_SPACING_EDGE",
  "compute_channel_offsets",
  "compute_nonconsecutive_channel_offsets",
  "get_tight_single_resource_liquid_op_offsets",
  "get_wide_single_resource_liquid_op_offsets",
  "required_spacing_between",
)


def __getattr__(name: str) -> Any:
  if name in _MOVED:
    warnings.warn(
      f"pylabrobot.legacy.liquid_handling.channel_positioning.{name} is deprecated and will be "
      f"removed in the future. It moved to pylabrobot.utils.liquid_handling.channel_positioning; "
      f"update your import to `from pylabrobot.utils.liquid_handling.channel_positioning import {name}`.",
      DeprecationWarning,
      stacklevel=2,
    )
    import importlib

    return getattr(
      importlib.import_module("pylabrobot.utils.liquid_handling.channel_positioning"), name
    )
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
