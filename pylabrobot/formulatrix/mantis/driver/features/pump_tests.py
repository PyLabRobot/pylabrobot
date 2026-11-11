"""Pump tests: the reads sent, against replies recorded from a Mantis v4."""

import json
import os
import tempfile
import unittest
from typing import List, Tuple

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.features.pump import PressureController
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  RECORDED_CALIBRATION,
  RECORDING,
  DriverTestCase,
  FakeLink,
  make_answers,
)

# The pressure board's reads, as sent.
CALIBRATION_READS = [(10, 20, bytes.fromhex(request)) for request in ("0000", "0100", "0200")]
# With the pump running: the chip controller enabled and the pump on, as recorded.
CHIP_CONTROLLER_ENABLED = (10, 12, bytes.fromhex("0100"), bytes.fromhex("0100"))
PUMP_ON = (10, 14, b"", bytes.fromhex("0100"))
# The chip and vacuum controllers' status while regulating: settled, then unsettled, as recorded.
CHIP_CONTROLLER_SETTLED = (10, 16, bytes.fromhex("0100"), bytes.fromhex("0100"))
VACUUM_CONTROLLER_UNSETTLED = (10, 16, bytes.fromhex("0200"), bytes.fromhex("0200"))


class PumpTests(DriverTestCase):
  async def asyncSetUp(self):
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)
    assert self.driver.pump is not None
    self.pump = self.driver.pump
    self.sent_before = len(self.link.written)

  def sent(self) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]]

  async def test_setup_reads_the_calibration_and_writes_nothing(self):
    self.assertEqual(self.driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)
    sent = [parse_frame(frame=frame)[1:] for frame in self.link.written[: self.sent_before]]
    self.assertEqual([read for read in sent if read[0] == 10], [(10, 1, b"")] + CALIBRATION_READS)

  async def test_measure_pressure(self):
    cases: List[Tuple[PressureController, str, float]] = [
      ("bottle", "0000", 1.1709537506103516),
      ("chip", "0100", 1.148512840270996),
      ("vacuum", "0200", -0.557586669921875),
    ]
    for controller, request, expected in cases:
      with self.subTest(controller=controller):
        self.assertEqual(await self.pump.measure_pressure(controller=controller), expected)
        self.assertEqual(self.sent()[-1], (10, 30, bytes.fromhex(request)))

  async def test_request_feedback_sensor_parameters(self):
    self.assertEqual(
      await self.pump.request_feedback_sensor_parameters(controller="vacuum"),
      RECORDED_CALIBRATION["vacuum"],
    )
    self.assertEqual(self.sent(), [CALIBRATION_READS[2]])

  async def test_pump_off_reads(self):
    self.assertEqual(await self.pump.request_controller_status(controller="bottle"), 0)
    self.assertFalse(await self.pump.request_controller_enabled(controller="bottle"))
    self.assertFalse(await self.pump.request_pump_on())
    self.assertEqual(self.sent(), [(10, 16, bytes(2)), (10, 12, bytes(2)), (10, 14, b"")])

  RUNNING_READS = (
    CHIP_CONTROLLER_ENABLED,
    PUMP_ON,
    CHIP_CONTROLLER_SETTLED,
    VACUUM_CONTROLLER_UNSETTLED,
  )

  async def test_pump_running_reads(self):
    for board, command, request, reply in self.RUNNING_READS:
      self.link.answers[(board, command, request)] = [reply]
    self.assertTrue(await self.pump.request_controller_enabled(controller="chip"))
    self.assertTrue(await self.pump.request_pump_on())
    self.assertEqual(await self.pump.request_controller_status(controller="chip"), 1)
    self.assertEqual(await self.pump.request_controller_status(controller="vacuum"), 2)
    self.assertEqual(
      self.sent(),
      [(board, command, request) for board, command, request, _ in self.RUNNING_READS],
    )

  async def test_unknown_controller_sends_nothing(self):
    for read in (
      self.pump.measure_pressure,
      self.pump.request_feedback_sensor_parameters,
      self.pump.request_controller_status,
      self.pump.request_controller_enabled,
    ):
      with self.subTest(read=read.__name__):
        with self.assertRaises(ValueError):
          await read(controller="air")  # type: ignore[arg-type]
    self.assertEqual(self.sent(), [])


class PumpDiscoveryTests(DriverTestCase):
  async def test_built_only_when_the_pressure_board_answers(self):
    for absent_boards, built in (((), True), ((10,), False)):
      with self.subTest(absent_boards=absent_boards):
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        self.assertEqual(driver.pump is not None, built)
        sent = [parse_frame(frame=frame)[1:] for frame in link.written]
        self.assertEqual(any(read in sent for read in CALIBRATION_READS), built)

  async def test_calibration_not_answered_leaves_it_empty(self):
    answers = make_answers()
    for board, command, request in CALIBRATION_READS:
      del answers[(board, command, request)]
    driver = await self.set_up_driver(link=FakeLink(answers=answers))
    self.assertIsNotNone(driver.pump)
    self.assertEqual(driver.configuration.feedback_sensor_parameters, {})

  async def test_declared_calibration_is_ignored_and_not_saved(self):
    calibration = {"bottle": [0.01, -14.0], "chip": [0.01, -14.0], "vacuum": [0.01, -38.0]}
    device = RECORDING["device"].items()
    without = {field: value for field, value in device if field != "feedback_sensor_parameters"}
    for device in (without, {**without, "feedback_sensor_parameters": calibration}):
      with self.subTest(declared="feedback_sensor_parameters" in device):
        with tempfile.TemporaryDirectory() as directory:
          path = os.path.join(directory, "declared.json")
          with open(path, "w", encoding="utf-8") as f:
            json.dump({**RECORDING, "device": device}, f)
          with self.assertNoLogs("pylabrobot.formulatrix.mantis", level="WARNING"):
            driver = await self.set_up_driver(
              link=FakeLink(answers=make_answers()), declared_configuration_json=path
            )
          self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)
          saved = os.path.join(directory, "saved.json")
          driver.save_configuration(path=saved)
          with open(saved, encoding="utf-8") as f:
            self.assertNotIn("feedback_sensor_parameters", json.load(f)["device"])

  async def test_recording_holds_the_recorded_calibration(self):
    with open(RECORDING_MANTIS_V4, encoding="utf-8") as f:
      declared = json.load(f)["device"]["feedback_sensor_parameters"]
    self.assertEqual(
      declared, {controller: list(value) for controller, value in RECORDED_CALIBRATION.items()}
    )


if __name__ == "__main__":
  unittest.main()
