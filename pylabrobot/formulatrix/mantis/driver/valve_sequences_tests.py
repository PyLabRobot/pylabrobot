"""Valve sequence tests against recorded Mantis v3 and v4 PPI writes."""

from typing import List, Tuple
from unittest import mock

import anyio
import pytest

from pylabrobot.formulatrix.mantis.driver.valve_sequences import (
  VALVE_BANK_PPI_ADDRESS,
  VALVE_SEQUENCES,
  _queue_valve_sequence,
)
from pylabrobot.testing.concurrency import AnyioTestBase

# The valve states a Mantis was sent, as (state, duration): a v4's Low Volume Hi-Fi pick-up,
# placement and dispenses, its High Volume Hi-Fi dispenses; a v3's High Volume dispenses.
SENT = {
  "low_volume_hi_fi": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_500nl": [(26, 104), (27, 1), (25, 12), (29, 19), (31, 10)],
    "dispense_100nl": [(30, 1), (22, 38), (23, 14), (21, 15), (29, 21), (31, 13)],
    "safe_state": [(17, 700)],
  },
  "high_volume": {
    "pre_attach": [(31, 100)],
    "pre_detach": [(16, 100), (18, 100), (30, 100), (31, 100)],
    "dispense_1ul": [(22, 34), (23, 14), (21, 15), (29, 21), (31, 13)],
    "dispense_5ul": [(26, 136), (27, 14), (25, 15), (29, 84), (31, 13)],
    "safe_state": [(17, 700)],
  },
  "high_volume_hi_fi": {
    "dispense_1ul": [(30, 15), (22, 34), (21, 15), (29, 21), (31, 13)],
    "dispense_5ul": [(30, 15), (26, 136), (25, 15), (29, 84), (31, 13)],
  },
}


class TestValveSequence(AnyioTestBase):
  def test_the_sequences_are_those_a_device_was_sent(self) -> None:
    for chip_type, sequences in SENT.items():
      for name, states in sequences.items():
        assert VALVE_SEQUENCES[chip_type][name] == states

  async def test_a_sequence_is_queued_state_by_state_on_the_valve_bank(self) -> None:
    queued_calls: List[Tuple[int, int, List[int]]] = []
    items = [7, 8, 9, 10]

    async def fake_queue_ppi_write(*, duration: int, start_address: int, values: List[int]) -> int:
      await anyio.lowlevel.checkpoint()
      queued_calls.append((duration, start_address, values))
      return items.pop(0)

    driver = mock.Mock()
    driver._queue_ppi_write = fake_queue_ppi_write
    item = await _queue_valve_sequence(
      driver=driver, chip_type="low_volume_hi_fi", name="pre_detach"
    )
    assert item == 10
    assert queued_calls == [
      (duration, VALVE_BANK_PPI_ADDRESS, [state])
      for state, duration in SENT["low_volume_hi_fi"]["pre_detach"]
    ]

  async def test_an_unknown_chip_type_or_sequence_queues_nothing(self) -> None:
    called = False

    async def fake_queue_ppi_write(*, duration: int, start_address: int, values: List[int]) -> int:
      nonlocal called
      del duration, start_address, values
      called = True
      await anyio.lowlevel.checkpoint()
      return 1

    driver = mock.Mock()
    driver._queue_ppi_write = fake_queue_ppi_write
    for chip_type, name in (("dome", "pre_attach"), ("low_volume", "wash_input")):
      with pytest.raises(ValueError):
        await _queue_valve_sequence(driver=driver, chip_type=chip_type, name=name)  # type: ignore[arg-type]
    assert not called
