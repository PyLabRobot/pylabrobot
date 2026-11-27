"""Each chip type's valve sequences, by name.

A sequence is a list of steps, each a pair (valve states, duration). The valve states are one number
with a bit per valve, and a set bit closes that valve: 1 fill, 2 outputs, 4 large, 8 small. Bit 16
is always set, so 31 closes every valve. The duration is in the sequencer's time steps, 1 ms on the
v4. Each step is written to ``VALVE_BANK_PPI_ADDRESS``. The durations are those for water; a liquid
class gives the drop, prime pump and reverse pump steps' own (``LIQUID_CLASS_DELAYS``).
"""

from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from ..liquid_classes import MantisLiquidClass

if TYPE_CHECKING:
  from .master import MantisDriver

VALVE_BANK_PPI_ADDRESS = 40
# Each chip type's sequences that only set valves, by name: (state, duration) per step.
VALVE_SEQUENCES: Dict[str, Dict[str, List[Tuple[int, int]]]] = {
  "low_volume": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_100nl": [(30, 12), (22, 27), (23, 14), (21, 15), (29, 21), (31, 13)],
    "dispense_500nl": [(26, 77), (27, 14), (25, 15), (29, 21), (31, 13)],
    "safe_state": [(17, 700)],
  },
  "high_volume": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_1ul": [(22, 34), (23, 14), (21, 15), (29, 21), (31, 13)],
    "dispense_5ul": [(26, 136), (27, 14), (25, 15), (29, 84), (31, 13)],
    "safe_state": [(17, 700)],
  },
  "low_volume_hi_fi": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_100nl": [(30, 1), (22, 38), (23, 14), (21, 15), (29, 21), (31, 13)],
    "dispense_500nl": [(26, 104), (27, 1), (25, 12), (29, 19), (31, 10)],
    "reverse_pump": [(17, 77), (19, 14), (18, 15), (30, 21), (31, 13)],
    "prime_pump": [(18, 77), (19, 14), (17, 15), (29, 21), (31, 13)],
    "safe_state": [(17, 700)],
  },
  "high_volume_hi_fi": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_1ul": [(30, 15), (22, 34), (21, 15), (29, 21), (31, 13)],
    "dispense_5ul": [(30, 15), (26, 136), (25, 15), (29, 84), (31, 13)],
    "reverse_pump": [(17, 84), (19, 15), (30, 14), (31, 136)],
    "safe_state": [(17, 700)],
  },
}
# The volume one play of a sequence moves, in uL, by chip type: the unit's PumpSequenceFiles.txt.
SEQUENCE_VOLUMES: Dict[str, Dict[str, float]] = {
  "high_volume": {"dispense_1ul": 1.0, "dispense_5ul": 5.0},
  "low_volume_hi_fi": {"dispense_100nl": 0.1, "dispense_500nl": 0.5, "reverse_pump": 0.6},
  "high_volume_hi_fi": {"dispense_1ul": 1.0, "dispense_5ul": 5.0, "reverse_pump": 6.0},
}
# The steps whose duration a liquid class gives (a fill or an empty), by chip type and sequence;
# a v3's high volume chip's as its drops show in 260602_dispense.
LIQUID_CLASS_DELAYS: Dict[str, Dict[str, Dict[int, Callable[[MantisLiquidClass], int]]]] = {
  "high_volume": {
    "dispense_1ul": {
      0: lambda liquid_class: liquid_class.small_fill_delay,
      3: lambda liquid_class: liquid_class.small_empty_delay,
    },
    "dispense_5ul": {
      0: lambda liquid_class: liquid_class.large_fill_delay,
      3: lambda liquid_class: liquid_class.large_empty_delay,
    },
  },
  "low_volume_hi_fi": {
    "dispense_100nl": {
      1: lambda liquid_class: liquid_class.small_fill_delay,
      4: lambda liquid_class: liquid_class.small_empty_delay,
    },
    "dispense_500nl": {
      0: lambda liquid_class: liquid_class.large_fill_delay,
      3: lambda liquid_class: liquid_class.large_empty_delay,
    },
    "reverse_pump": {
      0: lambda liquid_class: liquid_class.recovery_fill_delay,
      3: lambda liquid_class: liquid_class.recovery_empty_delay,
    },
    "prime_pump": {
      0: lambda liquid_class: liquid_class.large_fill_delay,
      3: lambda liquid_class: liquid_class.large_empty_delay,
    },
  },
  "high_volume_hi_fi": {
    "dispense_1ul": {
      1: lambda liquid_class: liquid_class.small_fill_delay,
      3: lambda liquid_class: liquid_class.small_empty_delay,
    },
    "dispense_5ul": {
      1: lambda liquid_class: liquid_class.large_fill_delay,
      3: lambda liquid_class: liquid_class.large_empty_delay,
    },
    "reverse_pump": {
      0: lambda liquid_class: liquid_class.recovery_fill_delay,
      2: lambda liquid_class: liquid_class.recovery_empty_delay,
    },
  },
}


async def _queue_valve_sequence(
  *,
  driver: "MantisDriver",
  chip_type: str,
  name: str,
  durations: Optional[Dict[int, int]] = None,
) -> Optional[int]:
  """Queue a chip type's valve sequence, state by state; return its last queue item, or None for a
  sequence with no state. The caller starts the sequencer and waits for that item.

  Args:
    driver: the driver whose sequencer plays it.
    chip_type: a key of ``VALVE_SEQUENCES``, as ``MantisChip.chip_type``.
    name: one of that chip type's sequences.
    durations: a duration by step index, in place of the sequence's own; None keeps them all.

  Raises:
    ValueError: if the chip type or the sequence is not one; nothing is queued.
  """
  if chip_type not in VALVE_SEQUENCES:
    raise ValueError(f"chip_type must be one of {sorted(VALVE_SEQUENCES)}, got {chip_type!r}")
  sequences = VALVE_SEQUENCES[chip_type]
  if name not in sequences:
    raise ValueError(f"name must be one of {sorted(sequences)}, got {name!r}")
  item: Optional[int] = None
  for index, (state, duration) in enumerate(sequences[name]):
    duration = (durations or {}).get(index, duration)
    item = await driver._queue_ppi_write(
      duration=duration, start_address=VALVE_BANK_PPI_ADDRESS, values=[state]
    )
  return item
