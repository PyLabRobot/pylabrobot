"""Drop sensor tests: the bytes sent, and the events counted, as a Mantis v4 sent them."""

import unittest

from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  DriverTestCase,
  FakeLink,
  make_answers,
)

# The sensor enabled, as every captured v4 startup sends it.
ENABLE_DROP_DETECTION = (12, 53, bytes.fromhex("01000000"))
# The tare's last frame: the chip line's solenoid valve closed.
TARE_END = (10, 34, bytes.fromhex("01000000"))
# A drop: the sensor's input turning on, then off, as a Mantis v4 sent it.
CAPTURED_DROP = (
  "100000000b0c000000002103000001003b0f",
  "100000000b0c000000002103000000003a0f",
)


class DropSensorDiscoveryTests(DriverTestCase):
  async def test_built_and_enabled_after_the_tare_only_on_a_v4_whose_board_12_answers(self):
    for absent_boards, built in (((), True), ((11,), False), ((12,), False)):
      with self.subTest(absent_boards=absent_boards):
        self.absent_boards = absent_boards
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        self.assertEqual(driver.drop_sensor is not None, built)
        sent = [parse_frame(frame=frame)[1:] for frame in link.written]
        enabled = [index for index, frame in enumerate(sent) if frame == ENABLE_DROP_DETECTION]
        self.assertEqual(len(enabled), int(built))
        if built:
          self.assertEqual(sent[enabled[0] - 1], TARE_END)


class DropCountTests(DriverTestCase):
  async def test_each_turn_on_of_the_input_counts_one_drop(self):
    driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert driver.drop_sensor is not None
    for frame in CAPTURED_DROP * 2:
      driver._handle_frame(frame=bytes.fromhex(frame))
    self.assertEqual(driver.drop_sensor._get_drop_count(), 2)


if __name__ == "__main__":
  unittest.main()
