"""Plate clamp tests: the bytes sent, against clamp states recorded from a Mantis v4."""

import unittest
from typing import List, Tuple
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisTimeoutError,
)
from pylabrobot.formulatrix.mantis.driver.features import plate_clamp
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import DriverTestCase, FakeLink, make_answers

RELEASE = (12, 31, bytes.fromhex("000000000000e83f000000000000000088130000"))
LOCK_AT_LEVEL_2 = (12, 30, bytes.fromhex("0000000000000000e17a14ae47e1ca3fe8030000"))
STATE_READ = (12, 33, b"")
# The states the device read after each command, as recorded.
STATES_AFTER_RELEASE_WHILE_RELEASED = ["0600", "0600"]
STATES_AFTER_RELEASE_WHILE_LOCKED = ["0500", "0600"]
STATES_AFTER_LOCK_AT_LEVEL_2 = ["0200"] * 7 + ["0300"]
STATES_AFTER_LOCK_AT_LEVEL_1 = ["0200"] * 14 + ["0300"]
# After power-on the clamp read 0, then 6 after the release stop sent.
STATES_AT_POWER_ON = ["0000"]
STATES_AFTER_RELEASE_AT_POWER_ON = ["0600"]


def answer_states_in_turn(*, link: FakeLink, turns: List[Tuple[int, List[str]]]) -> None:
  """When each board-12 command in ``turns`` is written, answer state reads with its states."""
  write = link.write

  async def write_then_answer_states(data: bytes) -> int:
    _, board, command, _ = parse_frame(frame=data)
    if turns and (board, command) == (12, turns[0][0]):
      _, states = turns.pop(0)
      link.answers[STATE_READ] = [bytes.fromhex(state) for state in states]
    return await write(data)

  link.io.write.side_effect = write_then_answer_states


class PlateClampTests(DriverTestCase):
  async def asyncSetUp(self):
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(
      link=self.link, declared_configuration_json=RECORDING_MANTIS_V4
    )
    assert self.driver.plate_clamp is not None
    self.clamp = self.driver.plate_clamp
    # every clamp command is answered; a short timeout would resend a read the runner was slow on
    self.driver.default_read_timeout = 5.0
    self.sent_before = len(self.link.written)

  def sent(self) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]]

  async def test_unlock(self):
    answer_states_in_turn(link=self.link, turns=[(31, STATES_AFTER_RELEASE_WHILE_LOCKED)])
    await self.clamp.unlock()
    self.assertEqual(self.sent(), [RELEASE, STATE_READ, STATE_READ])

  async def test_clamping_done_never_read_releases_and_raises(self):
    answer_states_in_turn(
      link=self.link,
      turns=[
        (31, STATES_AFTER_RELEASE_WHILE_RELEASED),
        (30, STATES_AFTER_LOCK_AT_LEVEL_1),
        (31, STATES_AFTER_RELEASE_WHILE_LOCKED),
      ],
    )
    # 9 polls end the wait before the recorded clamping done (the 15th read)
    with mock.patch.object(plate_clamp, "CLAMPING_TIMEOUT", 0.3):
      with self.assertRaisesRegex(MantisTimeoutError, "level 1; last state 2"):
        await self.clamp.lock(level=1)
    self.assertEqual(self.sent()[-3:], [RELEASE, STATE_READ, STATE_READ])

  async def test_lock_with_no_level_uses_default_lock_level(self):
    self.assertEqual(self.clamp.default_lock_level, 1)
    answer_states_in_turn(
      link=self.link,
      turns=[(31, STATES_AFTER_RELEASE_WHILE_RELEASED), (30, STATES_AFTER_LOCK_AT_LEVEL_2)],
    )
    self.clamp.default_lock_level = 2
    await self.clamp.lock()
    self.assertEqual(self.sent(), [RELEASE, STATE_READ, LOCK_AT_LEVEL_2] + [STATE_READ] * 8)

  async def test_unknown_default_lock_level_sends_nothing(self):
    self.clamp.default_lock_level = 4
    with self.assertRaises(ValueError):
      await self.clamp.lock()
    self.assertEqual(self.sent(), [])

  async def test_unknown_level_sends_nothing(self):
    with self.assertRaises(ValueError):
      await self.clamp.lock(level=4)
    self.assertEqual(self.sent(), [])

  async def test_stop_releases_a_locked_clamp(self):
    answer_states_in_turn(
      link=self.link,
      turns=[
        (31, STATES_AFTER_RELEASE_WHILE_RELEASED),
        (30, STATES_AFTER_LOCK_AT_LEVEL_2),
        (31, STATES_AFTER_RELEASE_WHILE_LOCKED),
      ],
    )
    await self.clamp.lock(level=2)
    await self.driver.stop()
    self.assertEqual(
      self.sent(),
      [RELEASE, STATE_READ, LOCK_AT_LEVEL_2]
      + [STATE_READ] * 8
      + [STATE_READ, RELEASE, STATE_READ, STATE_READ],
    )

  async def test_stop_releases_a_clamp_not_actuated_since_power_on(self):
    self.link.answers[STATE_READ] = [bytes.fromhex(state) for state in STATES_AT_POWER_ON]
    answer_states_in_turn(link=self.link, turns=[(31, STATES_AFTER_RELEASE_AT_POWER_ON)])
    await self.driver.stop()
    self.assertEqual(self.sent(), [STATE_READ, RELEASE, STATE_READ])


class PlateClampDiscoveryTests(DriverTestCase):
  async def test_lock_with_no_levels_sends_nothing(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    assert driver.plate_clamp is not None
    driver.plate_clamp.levels = None  # a unit whose folder, declared file and recording have none
    sent_before = len(link.written)
    with self.assertRaisesRegex(MantisConfigurationError, "no clamp levels"):
      await driver.plate_clamp.lock(level=1)
    self.assertEqual(len(link.written), sent_before)

  async def test_built_only_when_the_lc3_board_answers(self):
    for absent_boards, built in (((), True), ((12,), False)):
      with self.subTest(absent_boards=absent_boards):
        driver = await self.set_up_driver(
          link=FakeLink(answers=make_answers(absent_boards=absent_boards))
        )
        self.assertEqual(driver.plate_clamp is not None, built)


if __name__ == "__main__":
  unittest.main()
