"""Each chip type's valve sequences, by name.

A sequence is a list of steps, each a pair (valve states, duration). The valve states are one number
with a bit per valve, and a set bit closes that valve: 1 fill, 2 outputs, 4 large, 8 small. Bit 16
is always set, so 31 closes every valve. The duration is in the sequencer's time steps, 1 ms on the
v4. Each step is written to ``VALVE_BANK_PPI_ADDRESS``. The dispense durations are those for water.

Priming and the reverse pump come later: some of their durations depend on the liquid class.
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

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
    "safe_state": [(17, 700)],
  },
  "high_volume_hi_fi": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_1ul": [(30, 15), (22, 34), (21, 15), (29, 21), (31, 13)],
    "dispense_5ul": [(30, 15), (26, 136), (25, 15), (29, 84), (31, 13)],
    "safe_state": [(17, 700)],
  },
}


async def _queue_valve_sequence(
  *, driver: "MantisDriver", chip_type: str, name: str
) -> Optional[int]:
  """Queue a chip type's valve sequence, state by state; return its last queue item, or None for a
  sequence with no state. The caller starts the sequencer and waits for that item.

  Args:
    driver: the driver whose sequencer plays it.
    chip_type: a key of ``VALVE_SEQUENCES``, as ``MantisChip.chip_type``.
    name: one of that chip type's sequences.

  Raises:
    ValueError: if the chip type or the sequence is not one; nothing is queued.
  """
  if chip_type not in VALVE_SEQUENCES:
    raise ValueError(f"chip_type must be one of {sorted(VALVE_SEQUENCES)}, got {chip_type!r}")
  sequences = VALVE_SEQUENCES[chip_type]
  if name not in sequences:
    raise ValueError(f"name must be one of {sorted(sequences)}, got {name!r}")
  item: Optional[int] = None
  for state, duration in sequences[name]:
    item = await driver._queue_ppi_write(
      duration=duration, start_address=VALVE_BANK_PPI_ADDRESS, values=[state]
    )
  return item
