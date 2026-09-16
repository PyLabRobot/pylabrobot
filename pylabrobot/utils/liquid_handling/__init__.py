"""Liquid-handling planning any pipetting device can use: where channels go, and which can move together.

Pure and synchronous: no device, no I/O. A device supplies its channel spacings and executes the plan itself.
"""

from .batch_scheduling import (
  ChannelBatch,
  enumerate_valid_batches,
  is_valid_batch,
  log_batches,
  minimum_exact_cover,
  plan_batches,
  validate_channel_selections,
)
from .channel_positioning import (
  GENERIC_LH_MIN_SPACING_BETWEEN_CHANNELS,
  compute_channel_offsets,
  compute_nonconsecutive_channel_offsets,
  get_tight_single_resource_liquid_op_offsets,
  get_wide_single_resource_liquid_op_offsets,
  required_spacing_between,
)
from .errors import ChannelsDoNotFitError
