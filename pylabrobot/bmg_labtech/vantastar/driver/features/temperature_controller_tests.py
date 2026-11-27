"""Temperature controller tests over the driver's fake USB link, with status replies recorded from the
reader."""

import unittest
from collections import deque

from pylabrobot.bmg_labtech.vantastar.driver.master_tests import (
  INCUBATOR_OFF,
  INCUBATOR_ON_30,
  STATUS_IDLE,
  commands,
  make_driver,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  STATUS_AFTER_INCUBATOR_COMMAND,
  STATUS_HEATING,
)


class TemperatureControllerTests(unittest.IsolatedAsyncioTestCase):
  """Heating, stopping it and reading the temperature."""

  async def test_start_and_stop_send_the_incubator_commands(self):
    """Starting and stopping temperature control send Voyager's incubator commands and keep the
    target while heating."""
    driver = make_driver(lambda p: STATUS_IDLE)
    controller = driver.temperature_controller
    await controller.start_temperature_control(30)
    self.assertEqual(controller.target_temperature, 30)
    await controller.stop_temperature_control()
    self.assertIsNone(controller.target_temperature)
    self.assertEqual(
      [c for c in commands(driver) if c[0] == 0x06], [INCUBATOR_ON_30, INCUBATOR_OFF]
    )

  async def test_start_returns_once_temperatures_are_reported(self):
    """Starting returns only once a status reply reports temperatures again; the temperature is the
    first sensor's, 30.1 C."""
    replies = deque([STATUS_IDLE] + [STATUS_AFTER_INCUBATOR_COMMAND] * 3)
    driver = make_driver(lambda p: replies.popleft() if replies else STATUS_HEATING)
    await driver.temperature_controller.start_temperature_control(30)
    self.assertEqual(len(replies), 0)
    self.assertEqual(await driver.temperature_controller.measure_temperature(), 30.1)

  async def test_start_times_out_without_temperatures(self):
    """Starting raises TimeoutError when no status reply reports temperatures."""
    driver = make_driver(lambda p: STATUS_AFTER_INCUBATOR_COMMAND)
    driver.temperature_controller.default_sensor_timeout = 0
    with self.assertRaises(TimeoutError):
      await driver.temperature_controller.start_temperature_control(30)

  async def test_wait_for_temperature(self):
    """Waiting returns the temperature once it is within the tolerance of the target (30.1 C for
    30 C), and raises TimeoutError when it is not reached (30.1 C for 35 C)."""
    driver = make_driver(lambda p: STATUS_HEATING)
    controller = driver.temperature_controller
    await controller.start_temperature_control(30)
    self.assertEqual(await controller.wait_for_temperature(), 30.1)
    await controller.start_temperature_control(35)
    with self.assertRaises(TimeoutError):
      await controller.wait_for_temperature(timeout=0)

  async def test_wait_needs_a_target(self):
    """Waiting without a target raises RuntimeError."""
    driver = make_driver(lambda p: STATUS_IDLE)
    with self.assertRaises(RuntimeError):
      await driver.temperature_controller.wait_for_temperature()


if __name__ == "__main__":
  unittest.main()
