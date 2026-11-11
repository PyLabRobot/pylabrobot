"""Deprecated: moved to `pylabrobot.lib.liquid_handling.pipette_batch_scheduling`.

Which channels can reach their targets in one X/Y move is shared by
every multi-channel pipette device, not the legacy liquid handler's alone.
Importing from here still works and returns the very same objects, but warns.
"""

# TODO: Remove >2026-12

import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the names this module forwards, for type checkers and IDEs
  from pylabrobot.lib.liquid_handling.pipette_batch_scheduling import (  # noqa: F401
    ChannelBatch,
    enumerate_valid_batches,
    is_valid_batch,
    log_batches,
    minimum_exact_cover,
    plan_batches,
    validate_channel_selections,
  )

_MOVED = (
  "ChannelBatch",
  "enumerate_valid_batches",
  "is_valid_batch",
  "log_batches",
  "minimum_exact_cover",
  "plan_batches",
  "validate_channel_selections",
)


def __getattr__(name: str) -> Any:
  if name in _MOVED:
    warnings.warn(
      f"pylabrobot.legacy.liquid_handling.pipette_batch_scheduling.{name} is deprecated and will be "
      f"removed in the future. It moved to pylabrobot.lib.liquid_handling.pipette_batch_scheduling; "
      f"update your import to `from pylabrobot.lib.liquid_handling.pipette_batch_scheduling import {name}`.",
      DeprecationWarning,
      stacklevel=2,
    )
    import importlib

    return getattr(
      importlib.import_module("pylabrobot.lib.liquid_handling.pipette_batch_scheduling"), name
    )
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
