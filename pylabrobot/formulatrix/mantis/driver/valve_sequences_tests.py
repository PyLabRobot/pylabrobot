import unittest
from unittest import mock

from pylabrobot.formulatrix.mantis.driver.valve_sequences import (
  VALVE_BANK_PPI_ADDRESS,
  VALVE_SEQUENCES,
  _queue_valve_sequence,
)

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


class ValveSequenceTests(unittest.IsolatedAsyncioTestCase):
  def test_the_sequences_are_those_a_device_was_sent(self):
    for chip_type, sequences in SENT.items():
      for name, states in sequences.items():
        with self.subTest(chip_type=chip_type, name=name):
          self.assertEqual(VALVE_SEQUENCES[chip_type][name], states)

  async def test_a_sequence_is_queued_state_by_state_on_the_valve_bank(self):
    driver = mock.Mock()
    driver._queue_ppi_write = mock.AsyncMock(side_effect=[7, 8, 9, 10])
    item = await _queue_valve_sequence(
      driver=driver, chip_type="low_volume_hi_fi", name="pre_detach"
    )
    self.assertEqual(item, 10)
    self.assertEqual(
      driver._queue_ppi_write.await_args_list,
      [
        mock.call(duration=duration, start_address=VALVE_BANK_PPI_ADDRESS, values=[state])
        for state, duration in SENT["low_volume_hi_fi"]["pre_detach"]
      ],
    )

  async def test_an_unknown_chip_type_or_sequence_queues_nothing(self):
    driver = mock.Mock()
    driver._queue_ppi_write = mock.AsyncMock()
    for chip_type, name in (("dome", "pre_attach"), ("low_volume", "wash_input")):
      with self.subTest(chip_type=chip_type, name=name):
        with self.assertRaises(ValueError):
          await _queue_valve_sequence(driver=driver, chip_type=chip_type, name=name)
    driver._queue_ppi_write.assert_not_awaited()
