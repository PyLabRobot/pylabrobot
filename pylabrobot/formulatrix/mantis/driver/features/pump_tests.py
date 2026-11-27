"""Pump tests: the reads sent, against replies captured from a Mantis v4; the prime and the
recovery against what a Mantis v4 and a Mantis v3 sent."""

import asyncio
import json
import os
import struct
import tempfile
import unittest
from typing import Dict, List, Optional, Set, Tuple, Union
from unittest import mock

from pylabrobot.formulatrix.mantis.device import RECORDING_MANTIS_V4
from pylabrobot.formulatrix.mantis.driver.errors import (
  MantisConfigurationError,
  MantisPressureError,
  MantisPrimeError,
)
from pylabrobot.formulatrix.mantis.driver.features import arm as arm_module
from pylabrobot.formulatrix.mantis.driver.features.arm_tests import (
  CAPTURED_CHIP_SLOT_1_PICK_UP,
  CHIP_SLOT_1_PATH,
  CLOSE_QUEUED_MOVE_REPLY,
  HALT,
  IDLE_ENCODER_REPLIES,
  WASTE_PATH,
  ArmTestCase,
  RouteLink,
  V3Link,
  make_v3_answers,
  parse_captured_moves,
)
from pylabrobot.formulatrix.mantis.driver.features.drop_sensor_tests import CAPTURED_DROP
from pylabrobot.formulatrix.mantis.driver.features.pump import PressureController
from pylabrobot.formulatrix.mantis.driver.features.rfid_tests import LOW_VOLUME_HI_FI_TAG_REPLY
from pylabrobot.formulatrix.mantis.driver.framing import parse_frame
from pylabrobot.formulatrix.mantis.driver.master_tests import (
  CALIBRATION_WRITTEN_BACK,
  CHIP_LINE_VENT,
  PUMP_READ,
  RECORDED_CALIBRATION,
  RECORDING,
  DriverTestCase,
  FakeLink,
  make_answers,
)
from pylabrobot.formulatrix.mantis.driver.oem_config import (
  COMMON_SEQUENCE_DIRECTORY,
  WASTE_APPROACH_PATH_ADAPTER_FILE,
  WASTE_APPROACH_PATH_FILE,
  _load_unit_configuration,
)
from pylabrobot.formulatrix.mantis.driver.oem_config_tests import (
  HEAD,
  SYNTHETIC_V3_ARM_BLOCK,
  TAIL,
  V3_DEVICE_CONFIG,
  get_route_text,
  write_files,
)
from pylabrobot.resources.formulatrix.mantis_chips import get_chip_factory, mantis_chip_low_volume

# The pressure board's reads, as sent.
CALIBRATION_READS = [(10, 20, bytes.fromhex(request)) for request in ("0000", "0100", "0200")]
# With the pump running: the chip controller enabled and the pump on, as recorded.
CHIP_CONTROLLER_ENABLED = (10, 12, bytes.fromhex("0100"), bytes.fromhex("0100"))
PUMP_ON = (10, 14, b"", bytes.fromhex("0100"))
# The chip and vacuum controllers' status while regulating: settled, then unsettled, as recorded.
CHIP_CONTROLLER_SETTLED = (10, 16, bytes.fromhex("0100"), bytes.fromhex("0100"))
VACUUM_CONTROLLER_UNSETTLED = (10, 16, bytes.fromhex("0200"), bytes.fromhex("0200"))


# A Mantis v4's tare at startup (board, command, request, reply; "-" is empty): for each sensor it
# turns the controller off, vents the line, reads twice 0.3 s apart, lowers the offset, closes it.
CAPTURED_TARE = """
10:20:0000:0000004049d0853f000000e06f292bc0
10:12:0000:0000
10:13:00000000:-
10:32:00000000:-
10:34:00001027:-
10:30:0000:000000003abcf23f
10:30:0000:000000003abcf23f
10:21:00000000004049d0853f00000020f7802dc0:-
10:34:00000000:-
10:20:0100:00000080d1d5853f00000000b4572bc0
10:12:0100:0000
10:13:01000000:-
10:32:01000000:-
10:34:01001027:-
10:30:0100:00000000f808f23f
10:30:0100:00000000f808f23f
10:21:010000000080d1d5853f00000000d3982dc0:-
10:34:01000000:-
""".split()


# A Mantis v4 ending an operation, as captured: the pump read running, each controller disabled,
# the pump off, the reservoir valves open for 2 s and closed, the pump read off.
CAPTURED_RELEASE = """
10:14:-:0100
10:13:00000000:-
10:13:01000000:-
10:13:02000000:-
10:15:0000:-
10:34:0000204e:-
10:32:0000204e:-
10:34:00000000:-
10:32:00000000:-
10:14:-:0000
""".split()


class PumpTests(DriverTestCase):
  async def asyncSetUp(self):
    self.link = FakeLink(answers=make_answers())
    self.driver = await self.set_up_driver(link=self.link)
    assert self.driver.pump is not None
    self.pump = self.driver.pump
    self.sent_before = len(self.link.written)

  def sent(self) -> List[Tuple[int, int, bytes]]:
    return [parse_frame(frame=frame)[1:] for frame in self.link.written[self.sent_before :]]

  async def test_setup_reads_the_calibration_then_tares_as_recorded(self):
    exchanges = [
      (int(board), int(command), bytes.fromhex(request.strip("-")), bytes.fromhex(reply.strip("-")))
      for board, command, request, reply in (exchange.split(":") for exchange in CAPTURED_TARE)
    ]
    link = FakeLink(answers=make_answers())
    for board, command, request, _ in exchanges:
      link.answers[(board, command, request)] = [
        reply for *key, reply in exchanges if tuple(key) == (board, command, request)
      ]
    sleeps = self.pump_sleep.await_count
    driver = await self.set_up_driver(link=link)
    sent = [parse_frame(frame=frame)[1:] for frame in link.written]
    self.assertEqual(
      [frame for frame in sent if frame[0] == 10],
      [(10, 1, b"")] + CALIBRATION_READS + [exchange[:3] for exchange in exchanges],
    )
    # Twice 0.3 s per sensor; the calibration kept is the one read before.
    self.assertEqual(
      [call.kwargs["seconds"] for call in self.pump_sleep.await_args_list[sleeps:]], [0.3] * 4
    )
    self.assertEqual(driver.configuration.feedback_sensor_parameters, RECORDED_CALIBRATION)

  def answer_release(self) -> List[Tuple[int, int, bytes]]:
    """Answer the captured release; return its requests, in order."""
    requests = []
    for exchange in CAPTURED_RELEASE:
      board, command, request, reply = exchange.split(":")
      key = (int(board), int(command), bytes.fromhex(request.strip("-")))
      if key[1] != 14:
        self.link.answers[key] = [bytes.fromhex(reply.strip("-"))]
      requests.append(key)
    self.link.answers[(10, 14, b"")] = [bytes.fromhex("0100"), bytes.fromhex("0000")]
    return requests

  async def test_stop_releases_the_pressure_vents_the_chip_line_then_writes_back(self):
    requests = self.answer_release()
    sleeps = self.pump_sleep.await_count
    await self.driver.stop()
    sent = [frame for frame in self.sent() if frame[0] == 10]
    self.assertEqual(sent, requests + CHIP_LINE_VENT + CALIBRATION_WRITTEN_BACK)
    sleeps = [call.kwargs["seconds"] for call in self.pump_sleep.await_args_list[sleeps:]]
    self.assertEqual(sleeps, [2.0, 2.0])

  async def test_a_v3_has_no_reservoir_to_release(self):
    requests = self.answer_release()
    self.driver.configuration.generation = "v3"
    await self.pump._stop()
    released = [request for request in requests if request[1] not in (32, 34)]
    self.assertEqual(self.sent(), released + CALIBRATION_WRITTEN_BACK)

  async def test_with_a_chip_on_the_head_stop_does_not_vent_the_chip_line(self):
    with mock.patch.object(self.driver, "arm", mock.Mock(attached_chip=1)):
      await self.pump._stop()
    self.assertEqual(self.sent(), [PUMP_READ, *CALIBRATION_WRITTEN_BACK])

  async def test_stop_with_the_pump_off_writes_the_calibration_back_once(self):
    await self.pump._stop()
    await self.pump._stop()
    self.assertEqual(
      self.sent(),
      [PUMP_READ, *CHIP_LINE_VENT, *CALIBRATION_WRITTEN_BACK, PUMP_READ, *CHIP_LINE_VENT],
    )

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
        self.absent_boards = absent_boards
        link = FakeLink(answers=make_answers(absent_boards=absent_boards))
        driver = await self.set_up_driver(link=link)
        self.assertEqual(driver.pump is not None, built)
        sent = [parse_frame(frame=frame)[1:] for frame in link.written]
        self.assertEqual(any(read in sent for read in CALIBRATION_READS), built)

  async def test_calibration_not_answered_fails_the_tare(self):
    answers = make_answers()
    for board, command, request in CALIBRATION_READS:
      del answers[(board, command, request)]
    driver = self.make_driver(link=FakeLink(answers=answers))
    send_command = driver._send_command

    async def send(**kwargs):
      # Only the calibration reads, never answered, wait briefly: an answered read cannot time out.
      if (kwargs["board"], kwargs["command"]) == (10, 20):
        kwargs["timeout"] = 0.25
      return await send_command(**kwargs)

    with mock.patch.object(driver, "_send_command", side_effect=send):
      with self.assertRaises(TimeoutError):
        await driver.setup(skip_arm=True)
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


# The frames a Mantis v4 sent priming and recovering, as written to board 0 (sequencer 61, 62; a
# queued valve state 65 as address, state and duration; a queued move 68 as each motor's target,
# "-" held) and board 10 (each write's payload). Reads are left out; a valve state the queue was
# full for is left out, as it was sent again. "a | b *N" is the states a, b played N times.

# A 100.4 uL prime of a low volume Hi-Fi chip: 4 small and 200 large drops.
CAPTURED_PRIME_LOW_VOLUME_HI_FI = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000003440
10/13 01000100
10/11 01000000000000003440
10/13 02000100
10/11 02000000000000002ec0
10/13 02000100
10/11 02000000000000000000
10/11 020000000000000029c0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
68 - - -61.811023622047244
68 -23.154317512109415 40.336540148989464 -61.811023622047244
68 -38.963765283785115 54.111835306886775 -61.811023622047244
68 -43.772995879562316 64.29607679895277 -61.811023622047244
68 -51.49355633418472 76.11624480382535 -61.811023622047244
68 -51.54971403777015 87.83676970166538 -61.811023622047244
68 -52.06422696457573 89.18090048846733 -61.811023622047244
68 - - 1261.1023622047242
68 -48.623750510575164 90.60297564169927 1261.1023622047242
68 -48.623750510575164 90.60297564169927 1457.9527559055118
65 40 30 1 | 65 40 22 38 | 65 40 23 14 | 65 40 21 15 | 65 40 29 21 | 65 40 31 13 *4
65 40 26 104 | 65 40 27 1 | 65 40 25 12 | 65 40 29 19 | 65 40 31 10 *200
65 40 17 104
68 -48.623750510575164 90.60297564169927 1457.9527559055118
68 -48.623750510575164 90.60297564169927 1261.1023622047242
68 - - 1261.1023622047242
68 -52.06422696457573 89.18090048846733 -61.811023622047244
68 -51.54971403777015 87.83676970166538 -61.811023622047244
68 -51.49355633418472 76.11624480382535 -61.811023622047244
68 -43.772995879562316 64.29607679895277 -61.811023622047244
68 -38.963765283785115 54.111835306886775 -61.811023622047244
68 -23.154317512109415 40.336540148989464 -61.811023622047244
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 00000000
10/13 01000000
10/13 02000000
10/15 0000
10/34 0000204e
10/32 0000204e
10/34 00000000
10/32 00000000
"""

# A 300 uL prime of a high volume Hi-Fi chip: 60 large drops.
CAPTURED_PRIME_HIGH_VOLUME_HI_FI = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000003440
10/13 01000100
10/11 01000000000000002840
10/13 02000100
10/11 02000000000000002bc0
10/13 02000100
10/11 02000000000000000000
10/11 02000000000000002bc0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
68 - - -61.811023622047244
68 -23.154317512109415 40.336540148989464 -61.811023622047244
68 -38.963765283785115 54.111835306886775 -61.811023622047244
68 -43.772995879562316 64.29607679895277 -61.811023622047244
68 -51.49355633418472 76.11624480382535 -61.811023622047244
68 -51.54971403777015 87.83676970166538 -61.811023622047244
68 -52.06422696457573 89.18090048846733 -61.811023622047244
68 - - 1261.1023622047242
68 -48.623750510575164 90.60297564169927 1261.1023622047242
68 -48.623750510575164 90.60297564169927 1457.9527559055118
65 40 30 15 | 65 40 26 136 | 65 40 25 15 | 65 40 29 84 | 65 40 31 13 *60
65 40 17 136
68 -48.623750510575164 90.60297564169927 1457.9527559055118
68 -48.623750510575164 90.60297564169927 1261.1023622047242
68 - - 1261.1023622047242
68 -52.06422696457573 89.18090048846733 -61.811023622047244
68 -51.54971403777015 87.83676970166538 -61.811023622047244
68 -51.49355633418472 76.11624480382535 -61.811023622047244
68 -43.772995879562316 64.29607679895277 -61.811023622047244
68 -38.963765283785115 54.111835306886775 -61.811023622047244
68 -23.154317512109415 40.336540148989464 -61.811023622047244
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 00000000
10/13 01000000
10/13 02000000
10/15 0000
10/34 0000204e
10/32 0000204e
10/34 00000000
10/32 00000000
"""

# A 100 uL recovery from a low volume Hi-Fi chip: 167 plays of the reverse pump.
CAPTURED_RECOVERY_LOW_VOLUME_HI_FI = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000003440
10/13 01000100
10/11 01000000000000002840
10/13 02000100
10/11 020000000000000029c0
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 02000100
10/11 02000000000000000000
10/11 020000000000000029c0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
65 40 17 104 | 65 40 19 14 | 65 40 18 15 | 65 40 30 19 | 65 40 31 13 *167
65 40 17 104
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 00000000
10/13 01000000
10/13 02000000
10/15 0000
10/34 0000204e
10/32 0000204e
10/34 00000000
10/32 00000000
"""

# A 400 uL recovery from a high volume Hi-Fi chip: 67 plays of the reverse pump.
CAPTURED_RECOVERY_HIGH_VOLUME_HI_FI = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000003440
10/13 01000100
10/11 01000000000000002840
10/13 02000100
10/11 02000000000000002bc0
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 02000100
10/11 02000000000000000000
10/11 02000000000000002bc0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
65 40 17 60 | 65 40 19 15 | 65 40 30 30 | 65 40 31 136 *67
65 40 17 60
68 - - -
68 -0.5891965243621929 -0.2509165576955263 -
68 - - -61.811023622047244
68 -0.5891965243621929 -0.2509165576955263 -61.811023622047244
10/13 00000000
10/13 01000000
10/13 02000000
10/15 0000
10/34 0000204e
10/32 0000204e
10/34 00000000
10/32 00000000
"""
# The first pump start after a power-up, as a Mantis v4 sent it: every valve closed, the pump on,
# each line enabled with no target.
CAPTURED_FIRST_PUMP_START = """
65 40 31 10
10/15 0100
10/13 00000100
10/13 01000100
10/13 02000100
"""
# A 10 uL prime of a high volume chip, as a Mantis v3 sent it (260601_homethenprime): its chip
# pick-up left out; the pump stayed on after it, so no release follows.
CAPTURED_V3_PRIME_HIGH_VOLUME = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000002840
10/13 01000100
10/11 01000000000000003440
10/13 02000100
10/11 02000000000000002ec0
10/13 02000100
10/11 020000000000000029c0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
68 - - 0.0
68 -0.0034940754157651733 -0.003494075415749384 -
68 45.407299599195596 -45.697128856018324 -
68 59.9187740698037 -47.36532362634318 -
68 - - 0.0
68 72.74453091934284 -33.26641700660934 0.0
65 40 26 136 | 65 40 27 14 | 65 40 25 15 | 65 40 29 84 | 65 40 31 13 *2
65 40 17 136
68 72.74453091934284 -33.26641700660934 0.0
68 - - 0.0
68 59.9187740698037 -47.36532362634318 -
68 45.407299599195596 -45.697128856018324 -
68 -0.0034940754157651733 -0.003494075415749384 -
68 - - 0.0
68 - - -
68 -0.6092570076955263 -0.6092570076955263 -
68 - - 0.0
68 -0.6092570076955263 -0.6092570076955263 0.0
"""
# The pump off a Mantis v3 sent at the end of each capture: every line disabled, then the pump.
CAPTURED_V3_PUMP_OFF = """
10/13 00000000
10/13 01000000
10/13 02000000
10/15 0000
"""
# A v3 install's Dispense.config, as installed, naming the waste path no capture shows played, and
# that path (its MoveToWasteStation_Adapter.seq.txt), in the arm frame.
V3_DISPENSE_CONFIG = (
  HEAD
  + '    <add key="MoveToWasteStationSequenceFile" '
  + 'value="MoveToWasteStation_Adapter.seq.txt" />\r\n'
  + TAIL
)
V3_WASTE_ADAPTER_PATH: List[List[Optional[float]]] = [
  [None, None, -1.5],
  [15.0, 31.17, None],
  [66.399, 12.492, None],
  [97.432, -15.26, None],
  [108.428, -44.472, None],
  [None, None, -1.5],
  [119.756, -52.279, 5.191],
]
# The waste path the captured v3 played, its targets each line's with offsets 0: a v3 install's
# MoveToWasteStation.seq.txt, in the arm frame.
V3_WASTE_PATH: List[List[Optional[float]]] = [
  [None, None, 0.0],
  [15.0, 31.17, None],
  [62.353, 18.114, None],
  [83.589, 5.884, None],
  [None, None, 0.0],
  [116.113, 9.428, 0.0],
]
# A low volume Hi-Fi chip primed until drops, as a Mantis v4 sent it: the prime pump one cycle at a
# time, 115 times (260811_startup_dispense_0.5_2.5uL_shutdown, 244.8 s to 267.9 s; moves left out).
CAPTURED_DROP_DETECTION_PRIME = """
61
62
10/15 0100
10/13 00000100
10/11 00000000000000003440
10/13 01000100
10/11 01000000000000003440
10/13 02000100
10/11 02000000000000002ec0
10/13 02000100
10/11 02000000000000000000
10/11 020000000000000029c0
10/13 00000000
10/32 00000000
10/34 00001027
10/34 00000000
10/13 01000100
10/11 01000000000000002840
65 40 18 104 | 65 40 19 14 | 65 40 17 15 | 65 40 29 19 | 65 40 31 13 *115
"""
# In that prime the drop sensor saw a drop in each of the last 5 cycles, none before. After each
# of the first 4 that dropped the next cycle was queued this long after it played, s.
CAPTURED_DRY_CYCLES = 110
CAPTURED_DROP_CYCLE_GAPS = (0.4126, 0.4207, 0.4061, 0.4069)
# The link's time in that capture: a dry cycle's next was queued at most this long after it played.
CAPTURED_DRY_CYCLE_GAP = 0.1258
# The targets each line held before these captures, as their pump starts set them again.
CAPTURED_TARGETS_BEFORE: Dict[str, Dict[PressureController, float]] = {
  "drop_detection_prime_low_volume_hi_fi": {"bottle": 20.0, "chip": 20.0, "vacuum": -15.0},
  "prime_low_volume_hi_fi": {"bottle": 20.0, "chip": 20.0, "vacuum": -15.0},
  "prime_high_volume_hi_fi": {"bottle": 20.0, "chip": 12.0, "vacuum": -13.5},
  "recovery_low_volume_hi_fi": {"bottle": 20.0, "chip": 12.0, "vacuum": -12.5},
  "recovery_high_volume_hi_fi": {"bottle": 20.0, "chip": 12.0, "vacuum": -13.5},
  "v3_prime_high_volume": {"bottle": 12.0, "chip": 20.0, "vacuum": -15.0},
}
# A v4 install's inputs block, as its Input1.config and Input4.config give it: tubes, a low volume
# Hi-Fi default prime of 100.4 uL and a high volume Hi-Fi default recovery of 400 uL.
INPUTS_BLOCK = {
  "1": {
    "prime_volume": {"tube": {"low_volume_hi_fi": 100.4}, "pipette_tip": {}},
    "reagent_recovery_volume": {"tube": {"low_volume_hi_fi": 100.0}, "pipette_tip": {}},
  },
  "4": {
    "prime_volume": {"tube": {"high_volume_hi_fi": 264.0}, "pipette_tip": {}},
    "reagent_recovery_volume": {"tube": {"high_volume_hi_fi": 400.0}, "pipette_tip": {}},
  },
}
# Every captured queued move's speed and acceleration, device units: the arms', then Z's.
CAPTURED_ARM_PROFILE = (5555.560555555556, 833.3383333333333)
CAPTURED_Z_PROFILE = (2165.359330708661, 7874.020748031496)

Frame = Union[str, Tuple[Optional[float], ...]]


def expand_captured(*, captured: str) -> List[Frame]:
  """A captured stream, one frame each: a queued move as its targets, any other as its text."""
  frames: List[Frame] = []
  for line in captured.strip().splitlines():
    body, count = line.rsplit(" *", 1) if " *" in line else (line, "1")
    for frame in body.split(" | ") * int(count):
      if frame.startswith("68 "):
        frames.append(tuple(None if value == "-" else float(value) for value in frame.split()[1:]))
      else:
        frames.append(frame)
  return frames


class PumpLink(RouteLink):
  """As ``RouteLink``, and answers each write to the pressure board empty and each queued valve
  state as a queued move was, as recorded; every line's status reads settled, as recorded."""

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    settled = CHIP_CONTROLLER_SETTLED[3]
    for controller in ("0000", "0100", "0200"):
      self.answers[(10, 16, bytes.fromhex(controller))] = [settled]

  async def write(self, data: bytes) -> int:
    _, board, command, payload = parse_frame(frame=data)
    if board == 10 and command in (11, 13, 15, 32, 34):
      self.answers.setdefault((board, command, payload), [b""])
    if (board, command) == (0, 65):
      self.answers.setdefault((board, command, payload), [bytes.fromhex(CLOSE_QUEUED_MOVE_REPLY)])
    return await super().write(data)


class V3PumpLink(V3Link):
  """As ``V3Link``, and answers what ``PumpLink`` does: the sequencer, each queued move and valve
  state, each write to the pressure board; every line settled, the vacuum reading 0."""

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    for command in (61, 62, 64):
      self.answers[(0, command, b"")] = [bytes.fromhex("0000")]
    for controller in ("0000", "0100", "0200"):
      self.answers[(10, 16, bytes.fromhex(controller))] = [CHIP_CONTROLLER_SETTLED[3]]
    self.answers[(10, 30, bytes.fromhex("0200"))] = [bytes(8)]

  async def write(self, data: bytes) -> int:
    _, board, command, payload = parse_frame(frame=data)
    if board == 10 and command in (11, 13, 15, 32, 34):
      self.answers.setdefault((board, command, payload), [b""])
    if (board, command) in ((0, 65), (0, 68)):
      self.answers.setdefault((board, command, payload), [bytes.fromhex(CLOSE_QUEUED_MOVE_REPLY)])
    return await super().write(data)


class PrimeAndRecoveryTests(ArmTestCase):
  async def set_up_chip(self, *, chip_type: str, targets: Dict[PressureController, float]):
    link = PumpLink(answers=make_answers(), encoders=IDLE_ENCODER_REPLIES)
    driver = await self.set_up_arm(
      link=link, approach_paths={"waste": WASTE_PATH, "chip_slot_1": CHIP_SLOT_1_PATH}
    )
    driver.arm._initialized = True
    driver.arm.request_is_parked = mock.AsyncMock(return_value=True)  # type: ignore[method-assign]

    async def wait_and_see_a_drop(*, item: int, timeout: float) -> None:
      for frame in CAPTURED_DROP:  # every captured fixed prime's sensor saw drops
        driver._handle_frame(frame=bytes.fromhex(frame))

    driver._wait_for_queue_item = mock.AsyncMock(  # type: ignore[method-assign]
      side_effect=wait_and_see_a_drop
    )
    driver.arm._current_chip = 1
    driver.arm._attached_chip = get_chip_factory(chip_type=chip_type)("chip")
    driver.pump._valves_closed = True
    driver.pump._target_pressures = dict(targets)
    self.link: FakeLink = link
    return driver

  async def set_up_v3_chip(self, *, waste_path=V3_WASTE_PATH):
    """A v3 brought up through the v3 link with the captured unit's waste path, a high volume chip
    from slot 1 on the head, the lines' targets as before the capture."""
    patcher = mock.patch.object(arm_module, "MOTOR_STATUS_POLL_INTERVAL", 0)
    patcher.start()
    self.addCleanup(patcher.stop)
    path = os.path.join(self.directory, "v3.json")
    with open(path, "w", encoding="utf-8") as file:
      json.dump({"arm": SYNTHETIC_V3_ARM_BLOCK, "approach_paths": {"waste": waste_path}}, file)
    link = V3PumpLink(answers=make_v3_answers())
    driver = await self.set_up_driver(link=link, skip_arm=False, declared_configuration_json=path)
    assert driver.arm is not None and driver.pump is not None
    self.addCleanup(setattr, driver.arm, "_initialized", False)
    driver.arm.request_is_parked = mock.AsyncMock(return_value=True)  # type: ignore[method-assign]
    driver._wait_for_queue_item = mock.AsyncMock()  # type: ignore[method-assign]
    driver.arm._current_chip = 1
    driver.arm._attached_chip = get_chip_factory(chip_type="high_volume")("chip")
    driver.pump._target_pressures = dict(CAPTURED_TARGETS_BEFORE["v3_prime_high_volume"])
    self.link = link
    self.sent_before = len(link.written)
    return driver

  def sent_frames(self, *, link: FakeLink) -> List[Frame]:
    """What was written, as ``expand_captured`` gives a captured stream."""
    frames: List[Frame] = []
    for board, command, payload in self.sent(link):
      if board == 0 and command == 65:
        _, duration, address, count = struct.unpack_from("<hhhh", payload)
        states = struct.unpack_from(f"<{count}H", payload, 8)
        frames.append(f"65 {address} {' '.join(map(str, states))} {duration}")
      elif board == 0 and command == 68:
        values = struct.unpack("<9d", payload[-72:])
        for motor, profile in (
          (0, CAPTURED_ARM_PROFILE),
          (1, CAPTURED_ARM_PROFILE),
          (2, CAPTURED_Z_PROFILE),
        ):
          if values[motor * 3 + 1]:
            for sent, captured in zip(values[motor * 3 + 1 : motor * 3 + 3], profile):
              self.assertAlmostEqual(sent, captured, places=9)
        frames.append(tuple(values[m * 3] if values[m * 3 + 1] else None for m in range(3)))
      elif board == 0 and command in (61, 62, 64):
        frames.append(str(command))
      elif board == 10 and command in (11, 13, 15, 21, 32, 34):
        frames.append(f"10/{command} {payload.hex()}")
    return frames

  def assert_frames(self, *, sent: List[Frame], captured: List[Frame]) -> None:
    self.assertEqual(len(sent), len(captured))
    for index, (ours, theirs) in enumerate(zip(sent, captured)):
      with self.subTest(frame=index):
        if isinstance(theirs, tuple):
          assert isinstance(ours, tuple)
          self.assertEqual([value is None for value in ours], [value is None for value in theirs])
          for value, expected in zip(ours, theirs):
            if value is not None and expected is not None:
              self.assertAlmostEqual(value, expected, places=9)
        else:
          self.assertEqual(ours, theirs)

  async def test_a_prime_sends_the_captured_frames(self):
    cases = (
      ("low_volume_hi_fi", 100.4, CAPTURED_PRIME_LOW_VOLUME_HI_FI),
      ("high_volume_hi_fi", 300.0, CAPTURED_PRIME_HIGH_VOLUME_HI_FI),
    )
    for chip_type, volume, captured in cases:
      with self.subTest(chip_type=chip_type):
        driver = await self.set_up_chip(
          chip_type=chip_type, targets=CAPTURED_TARGETS_BEFORE[f"prime_{chip_type}"]
        )
        await driver.pump.prime_chip(chip_number=1, volume=volume)
        self.assert_frames(
          sent=self.sent_frames(link=self.link), captured=expand_captured(captured=captured)
        )
        self.assertEqual(driver.arm.current_chip, 1)
        self.assertTrue(driver.pump.is_primed)

  async def test_a_prime_picks_the_chip_up_first_as_captured(self):
    # The captured pick-up of slot 1's chip (its idle Z at the flag, see arm_tests), then the prime.
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["prime_low_volume_hi_fi"]
    )
    driver.arm._current_chip, driver.arm._attached_chip = None, None
    self.link.reader.tags = {0: LOW_VOLUME_HI_FI_TAG_REPLY}  # slot 1's antenna
    await driver.pump.prime_chip(chip_number=1, volume=100.4)
    pick_up: List[Frame] = ["61", "62"]
    for index, targets in enumerate(parse_captured_moves(captured=CAPTURED_CHIP_SLOT_1_PICK_UP)):
      if index in (1, len(CAPTURED_CHIP_SLOT_1_PICK_UP) - 1):
        targets = {**targets, 2: 0.0}
      pick_up.append(tuple(targets.get(motor) for motor in range(3)))
    self.assert_frames(
      sent=self.sent_frames(link=self.link),
      captured=pick_up + expand_captured(captured=CAPTURED_PRIME_LOW_VOLUME_HI_FI),
    )
    self.assertEqual(driver.arm.current_chip, 1)
    self.assertTrue(driver.pump.is_primed)

  async def test_a_v3_prime_sends_the_captured_v3_frames(self):
    driver = await self.set_up_v3_chip()
    await driver.pump.prime_chip(chip_number=1, volume=10.0)
    # The prime as captured; then it lets off the pump it started, as a v3 turns its pump off.
    self.assert_frames(
      sent=self.sent_frames(link=self.link),
      captured=expand_captured(captured=CAPTURED_V3_PRIME_HIGH_VOLUME)
      + expand_captured(captured=CAPTURED_V3_PUMP_OFF),
    )
    self.assertTrue(driver.pump.is_primed)

  async def test_a_v3_prime_not_captured_moves_nothing(self):
    # From where a prime left the head, with small drops, along the unit folder's waste path.
    driver = await self.set_up_v3_chip()
    driver.arm.request_is_parked.return_value = False
    with self.assertRaisesRegex(NotImplementedError, "a v3 primes only from idle"):
      await driver.pump.prime_chip(chip_number=1, volume=10.0)
    driver.arm.request_is_parked.return_value = True
    with self.assertRaisesRegex(NotImplementedError, "small drops, which no v3 prime"):
      await driver.pump.prime_chip(chip_number=1, volume=12.0)
    with tempfile.TemporaryDirectory() as directory:
      data = os.path.join(directory, "Data")
      configs = os.path.join(data, "Device", "Configs")
      sequences = os.path.join(data, COMMON_SEQUENCE_DIRECTORY)
      os.makedirs(configs)
      os.makedirs(sequences)
      write_files(
        directory=configs,
        files={"Device.config": V3_DEVICE_CONFIG, "Dispense.config": V3_DISPENSE_CONFIG},
      )
      write_files(
        directory=sequences,
        files={
          WASTE_APPROACH_PATH_FILE: get_route_text(route=V3_WASTE_PATH),
          WASTE_APPROACH_PATH_ADAPTER_FILE: get_route_text(route=V3_WASTE_ADAPTER_PATH),
        },
      )
      waste = _load_unit_configuration(oem_config_directory=configs)["approach_paths"]["waste"]
    self.assertEqual(waste, V3_WASTE_ADAPTER_PATH)
    driver = await self.set_up_v3_chip(waste_path=waste)
    with self.assertRaisesRegex(NotImplementedError, "waste path at Z's flag"):
      await driver.pump.prime_chip(chip_number=1, volume=10.0)
    self.assertEqual(self.sent_frames(link=self.link), [])

  async def test_a_fixed_prime_the_sensor_saw_no_drop_in_ends_as_captured_then_raises(self):
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["prime_low_volume_hi_fi"]
    )
    driver._wait_for_queue_item.side_effect = None
    with self.assertRaisesRegex(MantisPrimeError, "the drop sensor saw none"):
      await driver.pump.prime_chip(chip_number=1, volume=100.4)
    self.assert_frames(
      sent=self.sent_frames(link=self.link),
      captured=expand_captured(captured=CAPTURED_PRIME_LOW_VOLUME_HI_FI),
    )
    self.assertFalse(driver.pump.is_primed)

  async def test_a_recovery_sends_the_captured_frames(self):
    cases = (
      ("low_volume_hi_fi", 100.0, CAPTURED_RECOVERY_LOW_VOLUME_HI_FI),
      ("high_volume_hi_fi", 400.0, CAPTURED_RECOVERY_HIGH_VOLUME_HI_FI),
    )
    for chip_type, volume, captured in cases:
      with self.subTest(chip_type=chip_type):
        driver = await self.set_up_chip(
          chip_type=chip_type, targets=CAPTURED_TARGETS_BEFORE[f"recovery_{chip_type}"]
        )
        await driver.pump.recover_reagent(chip_number=1, volume=volume)
        self.assert_frames(
          sent=self.sent_frames(link=self.link), captured=expand_captured(captured=captured)
        )

  async def test_without_a_volume_the_default_sends_the_captured_frames(self):
    # The input slot's default from the unit's inputs block; without it, the chip's for a prime.
    cases = (
      ("low_volume_hi_fi", 1, "prime", INPUTS_BLOCK, CAPTURED_PRIME_LOW_VOLUME_HI_FI),
      ("low_volume_hi_fi", 1, "prime", None, CAPTURED_PRIME_LOW_VOLUME_HI_FI),
      ("high_volume_hi_fi", 4, "recovery", INPUTS_BLOCK, CAPTURED_RECOVERY_HIGH_VOLUME_HI_FI),
    )
    for chip_type, slot, operation, inputs, captured in cases:
      with self.subTest(operation=operation, inputs=inputs is not None):
        driver = await self.set_up_chip(
          chip_type=chip_type, targets=CAPTURED_TARGETS_BEFORE[f"{operation}_{chip_type}"]
        )
        if inputs is not None:
          driver._unit_blocks["inputs"] = inputs
        driver.arm._current_chip = slot
        if operation == "prime":
          await driver.pump.prime_chip(chip_number=slot, drop_detection=False)
        else:
          await driver.pump.recover_reagent(chip_number=slot)
        self.assert_frames(
          sent=self.sent_frames(link=self.link), captured=expand_captured(captured=captured)
        )

  async def test_a_pump_running_for_the_clamp_stays_on(self):
    # The pump is neither started nor stopped; at the end only the chip and vacuum lines go off.
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["recovery_low_volume_hi_fi"]
    )
    self.link.answers[(10, 14, b"")] = [PUMP_ON[3]]
    await driver.pump.recover_reagent(chip_number=1, volume=100.0)
    sent = self.sent_frames(link=self.link)
    self.assertNotIn("10/15 0100", sent)
    self.assertNotIn("10/15 0000", sent)
    self.assertEqual(sent[-2:], ["10/13 01000000", "10/13 02000000"])

  async def test_a_new_setup_closes_the_valves_again_before_the_next_pump_start(self):
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["prime_low_volume_hi_fi"]
    )
    await driver.pump._tare_pressure_sensors()
    self.sent_before = len(self.link.written)
    await driver.pump._start_pump()
    self.assertEqual(
      self.sent_frames(link=self.link), expand_captured(captured=CAPTURED_FIRST_PUMP_START)
    )
    self.assertEqual(
      [call.kwargs["seconds"] for call in self.pump_sleep.await_args_list[-1:]], [2.0]
    )

  async def test_what_cannot_be_primed_sends_nothing(self):
    driver = await self.set_up_chip(chip_type="low_volume_hi_fi", targets={})
    pump = driver.pump
    with self.assertRaisesRegex(ValueError, "volume must be more than 0"):
      await pump.prime_chip(chip_number=1, volume=0.0)
    with self.assertRaisesRegex(ValueError, "input_source must be one of"):
      await pump.recover_reagent(chip_number=1, input_source="bottle")  # type: ignore[arg-type]
    # A recovery does not pick up: another slot's chip, or none, refuses.
    with self.assertRaisesRegex(MantisConfigurationError, "from slot 2 on the head"):
      await pump.recover_reagent(chip_number=2, volume=6.0)
    with self.assertRaisesRegex(MantisConfigurationError, "no default reagent_recovery_volume"):
      await pump.recover_reagent(chip_number=1)
    driver.arm._attached_chip = mantis_chip_low_volume(name="chip")
    with self.assertRaisesRegex(MantisConfigurationError, "low_volume chip"):
      await pump.recover_reagent(chip_number=1, volume=6.0)
    driver.arm._current_chip, driver.arm._attached_chip = None, None
    with self.assertRaisesRegex(MantisConfigurationError, "holds the chip from slot None"):
      await pump.recover_reagent(chip_number=1, volume=6.0)
    driver.configuration.generation = "v3"
    with self.assertRaisesRegex(NotImplementedError, "no recovery has been captured on a v3"):
      await pump.recover_reagent(chip_number=1, volume=6.0)
    self.assertEqual(self.sent(self.link), [])

  async def test_a_line_that_does_not_settle_stops_and_releases(self):
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["prime_low_volume_hi_fi"]
    )
    self.link.answers[(10, 16, bytes.fromhex("0200"))] = [VACUUM_CONTROLLER_UNSETTLED[3]]
    with (
      mock.patch("pylabrobot.formulatrix.mantis.driver.features.pump.SETTLE_TIMEOUT", 0.0),
      self.assertRaisesRegex(MantisPressureError, "the vacuum line did not settle"),
    ):
      await driver.pump.prime_chip(chip_number=1, volume=100.4)
    self.assert_stopped_and_released()

  async def test_a_cancelled_prime_stops_and_releases(self):
    # A chip primed before is not primed once a prime of it fails.
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi", targets=CAPTURED_TARGETS_BEFORE["prime_low_volume_hi_fi"]
    )
    await driver.pump.prime_chip(chip_number=1, volume=100.4)
    self.assertTrue(driver.pump.is_primed)
    self.sent_before = len(self.link.written)
    driver._wait_for_queue_item.side_effect = asyncio.CancelledError
    with self.assertRaises(asyncio.CancelledError):
      await driver.pump.prime_chip(chip_number=1, volume=100.4)
    self.assert_stopped_and_released()
    self.assertFalse(driver.pump.is_primed)

  async def test_a_refused_prime_of_a_chip_in_its_slot_reads_its_tag_only(self):
    driver = await self.set_up_chip(chip_type="low_volume_hi_fi", targets={})
    driver.arm._current_chip, driver.arm._attached_chip = None, None
    self.link.reader.tags = {0: LOW_VOLUME_HI_FI_TAG_REPLY}  # slot 1's antenna
    with self.assertRaisesRegex(ValueError, "consecutive_drops must be at most 400"):
      await driver.pump.prime_chip(chip_number=1, drop_detection=True, consecutive_drops=500)
    self.assertEqual(self.sent_frames(link=self.link), [])
    self.assertIsNone(driver.arm.current_chip)

  def drop_in_cycles(self, *, driver, cycles: Set[int]) -> None:
    """Hand the driver the captured drop's events as each cycle in ``cycles`` (from 1) plays."""
    waits = 0

    async def wait_for_queue_item(*, item: int, timeout: float) -> None:
      nonlocal waits
      waits += 1  # the first is the move into the waste, then one per cycle
      if waits - 1 in cycles:
        for frame in CAPTURED_DROP:
          driver._handle_frame(frame=bytes.fromhex(frame))

    driver._wait_for_queue_item.side_effect = wait_for_queue_item

  async def test_a_drop_detection_prime_sends_the_captured_cycles_and_waits(self):
    # Without a volume, a v4 with a drop sensor primes a low volume Hi-Fi chip until drops.
    driver = await self.set_up_chip(
      chip_type="low_volume_hi_fi",
      targets=CAPTURED_TARGETS_BEFORE["drop_detection_prime_low_volume_hi_fi"],
    )
    self.drop_in_cycles(driver=driver, cycles=set(range(CAPTURED_DRY_CYCLES + 1, 116)))
    sleeps = self.pump_sleep.await_count
    await driver.pump.prime_chip(chip_number=1)
    # The moves into the waste come before the cycles; the capture's came before the pressures.
    sent: List[Frame] = [
      frame for frame in self.sent_frames(link=self.link) if not isinstance(frame, tuple)
    ]
    captured = expand_captured(captured=CAPTURED_DROP_DETECTION_PRIME)
    self.assert_frames(sent=sent[: len(captured)], captured=captured)
    # No wait after a dry cycle; after one that dropped, within the link's time of the capture's.
    waits = [call.kwargs["seconds"] for call in self.pump_sleep.await_args_list[sleeps:]]
    cycle_waits = waits[3:-1]  # after the pump's start, the vacuum and the bottle; before the end
    self.assertEqual(len(cycle_waits), 2 * 5)
    for drop, gap in enumerate(CAPTURED_DROP_CYCLE_GAPS):
      with self.subTest(drop=drop):
        waited = sum(cycle_waits[2 * drop : 2 * drop + 2])
        self.assertLessEqual(gap - CAPTURED_DRY_CYCLE_GAP, waited)
        self.assertLessEqual(waited, gap)
    self.assertEqual(driver.drop_sensor.consecutive_run, 5)
    self.assertTrue(driver.pump.is_primed)

  async def test_a_drop_detection_prime_needs_its_drops_in_a_row_and_raises_at_the_cap(self):
    prime_pump_cycle = "65 40 18 104"
    cases = ((range(1, 7), True), ({1, 2, 4, 5, 6}, True), ({1, 2, 4, 5}, False))
    for cycles, ends_on_drops in cases:
      with self.subTest(cycles=cycles):
        driver = await self.set_up_chip(chip_type="low_volume_hi_fi", targets={})
        self.drop_in_cycles(driver=driver, cycles=set(cycles))
        prime = driver.pump.prime_chip(
          chip_number=1, drop_detection=True, consecutive_drops=3, max_cycles=6
        )
        if ends_on_drops:
          await prime
        else:
          with self.assertRaisesRegex(MantisPrimeError, "in 3 prime pump cycles in a row"):
            await prime
        sent = self.sent_frames(link=self.link)
        played = sent.count(prime_pump_cycle)
        self.assertEqual(played, 3 if cycles == range(1, 7) else 6)
        # The prime ends the same either way: the safe state, the way out, the pressure released.
        self.assertEqual(sent[-1], "10/32 00000000")
        self.assertIn("65 40 17 104", sent)
        self.assertEqual(driver.pump.is_primed, ends_on_drops)

  async def test_what_cannot_be_primed_until_drops_sends_nothing(self):
    driver = await self.set_up_chip(chip_type="low_volume_hi_fi", targets={})
    pump = driver.pump
    with self.assertRaisesRegex(ValueError, "takes no volume"):
      await pump.prime_chip(chip_number=1, volume=6.0, drop_detection=True)
    for consecutive_drops, max_cycles in ((0, None), (401, None), (2.5, None), (5, 0)):
      with self.assertRaisesRegex(ValueError, "must be"):
        await pump.prime_chip(
          chip_number=1,
          drop_detection=True,
          consecutive_drops=consecutive_drops,  # type: ignore[arg-type]
          max_cycles=max_cycles,
        )
    driver.arm._attached_chip = get_chip_factory(chip_type="high_volume_hi_fi")("chip")
    with self.assertRaisesRegex(MantisConfigurationError, "primed until drops"):
      await pump.prime_chip(chip_number=1, drop_detection=True)
    driver.drop_sensor = None
    with self.assertRaisesRegex(MantisConfigurationError, "no drop sensor"):
      await pump.prime_chip(chip_number=1, drop_detection=True)
    self.assertEqual(self.sent(self.link), [])

  def assert_stopped_and_released(self) -> None:
    sent = self.sent(self.link)
    halt = [frame for frame in sent if frame in HALT]
    self.assertEqual(halt, HALT)
    release = [
      f"10/{command} {payload.hex()}"
      for board, command, payload in sent[sent.index(HALT[-1]) :]
      if board == 10 and command in (13, 15, 32, 34)
    ]
    self.assertEqual(
      release,
      [
        "10/13 00000000",
        "10/13 01000000",
        "10/13 02000000",
        "10/15 0000",
        "10/34 0000204e",
        "10/32 0000204e",
        "10/34 00000000",
        "10/32 00000000",
      ],
    )


if __name__ == "__main__":
  unittest.main()
