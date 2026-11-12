"""Drop sensor tests: enabled at setup on a v4, and the drops it reports counted, against frames
recorded from a Mantis v4."""

import unittest
from typing import List
from unittest import mock

from pylabrobot.formulatrix.mantis.driver.features import drop_sensor as drop_sensor_module
from pylabrobot.formulatrix.mantis.driver.features.drop_sensor import DROP_GAP, DropSensor
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  DROP_SENSOR_ENABLED,
  DriverTestCase,
  FakeLink,
  make_answers,
)

# The drop sensor's input turning on, then off, during a prime on a v4.
RECORDED_DROP_ON = "10000000b101000000002103000001008102"
RECORDED_DROP_OFF = "10000000b401000000002103000000008502"


class FakeClock:
  """A clock that stands still until moved."""

  def __init__(self):
    self.now = 100.0

  def monotonic(self) -> float:
    return self.now


class DropSensorSetupTests(DriverTestCase):
  async def test_enabled_once_at_setup_on_a_v4(self):
    link = FakeLink(answers=make_answers())
    driver = await self.set_up_driver(link=link)
    self.assertIsInstance(driver.drop_sensor, DropSensor)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual(sent.count(DROP_SENSOR_ENABLED), 1)

  async def test_none_without_board_12_or_on_a_v3(self):
    for absent_boards in ((12,), (11,)):
      with self.subTest(absent_boards=absent_boards):
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        self.assertIsNone(driver.drop_sensor)
        self.assertNotIn(53, [parse_frame(frame=frame)[3] for frame in link.written])


class DropCountTests(DriverTestCase):
  async def asyncSetUp(self):
    self.driver = await self.set_up_driver(link=FakeLink(answers=make_answers()))
    assert self.driver.drop_sensor is not None
    self.sensor = self.driver.drop_sensor
    self.clock = FakeClock()
    patch = mock.patch.object(drop_sensor_module, "time", self.clock)
    patch.start()
    self.addCleanup(patch.stop)

  def receive(self, *, frames: List[str]) -> None:
    for frame in frames:
      self.driver._handle_frame(frame=bytes.fromhex(frame))

  async def test_each_drop_counts_and_the_input_turning_off_does_not(self):
    self.assertFalse(self.sensor.drop_detected)
    for _ in range(3):
      self.receive(frames=[RECORDED_DROP_ON, RECORDED_DROP_OFF])
      self.clock.now += 0.18
    self.assertTrue(self.sensor.drop_detected)
    self.assertEqual(self.sensor.consecutive_drops, 3)

  async def test_a_gap_starts_the_run_again(self):
    self.receive(frames=[RECORDED_DROP_ON, RECORDED_DROP_ON])
    self.clock.now += DROP_GAP + 0.01
    self.receive(frames=[RECORDED_DROP_ON])
    self.assertEqual(self.sensor.consecutive_drops, 1)

  async def test_reset_forgets_every_drop(self):
    self.receive(frames=[RECORDED_DROP_ON])
    self.sensor.reset()
    self.assertFalse(self.sensor.drop_detected)
    self.assertEqual(self.sensor.consecutive_drops, 0)


if __name__ == "__main__":
  unittest.main()
