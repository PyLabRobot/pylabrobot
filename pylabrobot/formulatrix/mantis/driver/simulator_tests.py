"""Simulated Mantis tests."""

import json
import os
import tempfile
import unittest

from pylabrobot.formulatrix.mantis import Mantis
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisTimeoutError
from pylabrobot.formulatrix.mantis.driver.master import SEQUENCER_QUEUE_SIZE
from pylabrobot.formulatrix.mantis.driver.simulator import MantisSimulationLedBar


class SimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.mantis = Mantis(simulation=True)
    await self.mantis.setup()

  async def test_setup_reports_the_recorded_generation(self):
    self.assertEqual(self.mantis.driver.configuration.generation, "v4")

  async def test_clamp_starts_idle_then_locks_and_unlocks(self):
    clamp = self.mantis.plate_clamp
    assert clamp is not None
    self.assertEqual(await clamp.request_state(), 0)
    await clamp.lock(level=3)
    self.assertEqual(await clamp.request_state(), 3)
    await clamp.unlock()
    self.assertEqual(await clamp.request_state(), 6)

  async def test_stop_releases_a_locked_clamp(self):
    clamp = self.mantis.plate_clamp
    assert clamp is not None
    await clamp.lock(level=3)
    await self.mantis.stop()
    await self.mantis.setup()
    self.assertEqual(await clamp.request_state(), 6)

  async def test_board_the_recording_declares_absent_does_not_answer(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    recording["device"]["firmware_versions"]["11"] = None
    recording["device"]["generation"] = "v3"
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "recording.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      await mantis.setup()
      self.addAsyncCleanup(mantis.stop)
    self.assertEqual(mantis.driver.configuration.generation, "v3")
    self.assertIsNone(mantis.led_bar)
    with self.assertRaises(MantisTimeoutError):
      await mantis.driver.request_firmware_version(board=11)

  async def test_rfid_reads_every_declared_slot_empty(self):
    rfid = self.mantis.rfid
    assert rfid is not None
    self.assertEqual(await rfid.request_tags(), {slot: None for slot in range(1, 7)})

  async def test_reads_answer_from_the_configuration(self):
    self.assertEqual(await self.mantis.driver.request_firmware_version(board=12), "0.1.8 DBG")
    self.assertEqual(await self.mantis.driver.request_motor_limits(motor=2), (-160.0, 2300.0))
    with self.assertRaisesRegex(NotImplementedError, "name is not simulated"):
      await self.mantis.driver.request_name()

  async def test_motors_read_idle_at_0(self):
    driver = self.mantis.driver
    for motor in (0, 1, 2):
      with self.subTest(motor=motor):
        self.assertEqual(await driver.request_motor_status(motor=motor), 0)
        self.assertEqual(await driver.request_motor_position(motor=motor), 0.0)
        self.assertEqual(await driver.request_encoder_position(motor=motor), 0.0)
    with self.assertRaisesRegex(NotImplementedError, "absolute encoders are not simulated"):
      await driver.request_absolute_encoder_position(motor=0)
    with self.assertRaises(ValueError):
      await driver.request_motor_status(motor=3)

  async def test_pump_reads_off_with_the_recorded_calibration(self):
    pump = self.mantis.pump
    assert pump is not None
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recorded = json.load(f)["device"]["feedback_sensor_parameters"]
    self.assertEqual(
      self.mantis.driver.configuration.feedback_sensor_parameters,
      {controller: tuple(value) for controller, value in recorded.items()},
    )
    self.assertEqual(
      await pump.request_feedback_sensor_parameters(controller="vacuum"),
      tuple(recorded["vacuum"]),
    )
    self.assertEqual(await pump.measure_pressure(controller="chip"), 0.0)
    self.assertEqual(await pump.request_controller_status(controller="chip"), 0)
    self.assertFalse(await pump.request_controller_enabled(controller="chip"))
    self.assertFalse(await pump.request_pump_on())
    with self.assertRaises(ValueError):
      await pump.measure_pressure(controller="air")  # type: ignore[arg-type]

  async def test_an_older_declared_file_takes_what_it_lacks_from_the_recording(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      recording = json.load(f)
    del recording["device"]["feedback_sensor_parameters"]
    del recording["led_bar"]
    with tempfile.TemporaryDirectory() as directory:
      path = os.path.join(directory, "older.json")
      with open(path, "w", encoding="utf-8") as f:
        json.dump(recording, f)
      mantis = Mantis(simulation=True, declared_configuration_json=path)
      with self.assertLogs("pylabrobot.formulatrix.mantis.driver.master", level="WARNING") as logs:
        await mantis.setup()
      self.addAsyncCleanup(mantis.stop)
    self.assertEqual(len(logs.output), 1)
    self.assertIn(f"led_bar from {RECORDING_MANTIS_V4}", logs.output[0])
    assert mantis.pump is not None and mantis.led_bar is not None
    self.assertEqual(mantis.led_bar.number_of_leds, 20)
    self.assertEqual(
      await mantis.pump.request_feedback_sensor_parameters(controller="bottle"),
      (0.010651180520653725, -13.580931663513184),
    )

  async def test_led_bar_takes_every_command(self):
    bar = self.mantis.led_bar
    assert isinstance(bar, MantisSimulationLedBar)
    self.assertEqual(bar.number_of_leds, 20)
    await bar.set_color(color="#FF0080")
    await bar.set_color(color=["green"] * 10 + ["off"] * 10)
    await bar.start_animation(effect="wave")
    await bar.stop_animation()
    await bar.turn_off()
    with self.assertRaises(ValueError):
      await bar.set_color(color="purple")

  async def test_sequencer_plays_what_is_queued_once_started(self):
    driver = self.mantis.driver
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    self.assertEqual(item, 1)
    with self.assertRaises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)
    self.assertIsNone(driver._sequence_progress)
    await driver._unchecked_fw_start_sequencer()
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._wait_for_queue_item(item=item, timeout=1.0)
    self.assertEqual(driver._sequence_progress, item)
    await driver._unchecked_fw_stop_sequencer()
    item = await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    with self.assertRaises(MantisTimeoutError):
      await driver._wait_for_queue_item(item=item, timeout=1.0)

  async def test_stopped_sequencer_fills_its_queue_and_a_clear_empties_it(self):
    driver = self.mantis.driver
    free_slots = [
      (
        await driver._unchecked_fw_queue_write_ppi(
          item=item, duration=10, start_address=40, values=[31]
        )
      )[0]
      for item in range(1, SEQUENCER_QUEUE_SIZE + 2)
    ]
    self.assertEqual(free_slots, list(range(SEQUENCER_QUEUE_SIZE - 1, -2, -1)))
    driver.default_queue_full_timeout = 0.01
    with self.assertRaisesRegex(MantisTimeoutError, "stayed full"):
      await driver._queue_ppi_write(duration=10, start_address=40, values=[31])
    await driver._unchecked_fw_clear_sequencer()
    await driver._queue_ppi_write(duration=10, start_address=40, values=[31])

  async def test_simulated_mantis_sends_nothing(self):
    with self.assertRaisesRegex(NotImplementedError, "sends nothing"):
      await self.mantis.driver._send_command(board=0, command=3)


if __name__ == "__main__":
  unittest.main()
