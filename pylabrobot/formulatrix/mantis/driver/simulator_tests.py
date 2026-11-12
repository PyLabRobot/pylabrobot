"""Simulated Mantis tests."""

import json
import os
import tempfile
import unittest

from pylabrobot.formulatrix.mantis import Mantis
from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import MantisTimeoutError


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

  async def test_simulated_mantis_sends_nothing(self):
    with self.assertRaisesRegex(NotImplementedError, "sends nothing"):
      await self.mantis.driver._send_command(board=0, command=3)


if __name__ == "__main__":
  unittest.main()
